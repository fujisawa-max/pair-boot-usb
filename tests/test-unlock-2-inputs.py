"""Synthetic installer and GPU-policy checks without firmware or physical GPUs."""
import io
import json
from pathlib import Path
import runpy
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FW = runpy.run_path(str(ROOT / 'scripts/prepare-firmware.py'))
POLICY = json.loads((ROOT / 'config/gpu-policy.json').read_text())
FLEET = runpy.run_path(str(ROOT / 'runtime/fleet-registry.py'))
DIAG = runpy.run_path(str(ROOT / 'runtime/gpu-unlock-diagnostic.py'))


class Unlock2Tests(unittest.TestCase):
    def test_upstream_lock_rejects_other_versions_commits_and_directories(self):
        upstream = runpy.run_path(str(ROOT / 'scripts/prepare-upstream.py'))
        expected = json.loads((ROOT / 'inputs/gpu-unlock-source.json').read_text())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'inputs').mkdir()
            path = root / 'inputs/gpu-unlock-source.json'
            for field, value in (('commit', '0' * 40), ('version', '1.0.0'),
                                 ('directory', '../outside'), ('url', 'http://example.invalid/source')):
                path.write_text(json.dumps({**expected, field: value}))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    upstream['source_lock'](root)

    def test_signing_refuses_old_unlock_even_with_the_same_kernel(self):
        signing = runpy.run_path(str(ROOT / 'scripts/sign-gpu-modules.py'))
        with tempfile.TemporaryDirectory() as folder:
            module = Path(folder) / 'gpu_unlock.ko'; module.write_bytes(b'fixture')
            info = {'name': 'gpu_unlock', 'version': '2.0.0', 'vermagic': signing['KERNEL'] + ' SMP',
                    'signer': '', 'sig_id': ''}
            function = signing['source_metadata']
            with patch.dict(function.__globals__, SIGN_FILE=module, SOURCES={'gpu_unlock': module}, metadata=lambda _: info):
                self.assertEqual(function()['gpu_unlock']['info']['version'], '2.0.0')
                info['version'] = '1.0.0'
                with self.assertRaisesRegex(ValueError, 'version mismatch'):
                    function()

    def test_installer_reads_only_expected_regular_unique_members_without_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            installer = Path(folder) / 'fixture.run'
            for kind in ('valid', 'link', 'duplicate', 'missing', 'wrong-version', 'truncated', 'broken-zstd'):
                data = io.BytesIO()
                with tarfile.open(fileobj=data, mode='w') as archive:
                    for name in ('firmware/gsp_ga10x.bin', 'firmware/gsp_tu10x.bin', 'LICENSE', '../ignored'):
                        if kind == 'missing' and name == 'LICENSE':
                            continue
                        for _ in range(2 if kind == 'duplicate' and name == 'LICENSE' else 1):
                            item = tarfile.TarInfo(name); item.size = 3
                            if kind == 'link' and name == 'LICENSE':
                                item.type = tarfile.SYMTYPE; item.linkname = '/outside'; item.size = 0
                            archive.addfile(item, io.BytesIO(b'abc'))
                compressed = subprocess.run(['zstd', '-cq'], input=data.getvalue(), capture_output=True, check=True).stdout
                header = b'#!/bin/sh\nversion_string=610.43.03\nskip=5\nexit 99\n'
                if kind == 'wrong-version':
                    header = header.replace(b'610.43.03', b'615.71.10')
                elif kind == 'truncated':
                    header = header.replace(b'skip=5', b'skip=99999')
                elif kind == 'broken-zstd':
                    compressed = b'not a compressed archive'
                installer.write_bytes(header + compressed)
                if kind == 'valid':
                    self.assertEqual(FW['read_installer_members'](installer),
                                     {name: b'abc' for name in ('gsp_ga10x.bin', 'gsp_tu10x.bin', 'NVIDIA-LICENSE')})
                else:
                    with self.subTest(kind=kind), self.assertRaises((ValueError, tarfile.TarError)):
                        FW['read_installer_members'](installer)
                self.assertEqual(installer.read_bytes(), header + compressed)

    def test_experimental_policy_and_diagnostic_never_accept_a_different_gpu(self):
        self.assertEqual(set(POLICY['experimental_unlock_ids']),
                         {pci for pci in POLICY['unlock_ids'] if 'RTX 20' in POLICY['products'][pci] or 'RTX 40' in POLICY['products'][pci]})
        self.assertLessEqual(FLEET['GPU_UNLOCK_IDS'], FLEET['GPU_NORMAL_IDS'])
        for pci in ('1e04', '2504', '2684'):
            profile = {'gpu_pci_id': '10de:' + pci}
            DIAG['qualify_gpu'](profile, '0x10de', '0x' + pci, FLEET['GPU_UNLOCK_IDS'])
            with self.assertRaises(ValueError):
                DIAG['qualify_gpu'](profile, '0x10de', '0x2b85', FLEET['GPU_UNLOCK_IDS'])
            with self.assertRaises(ValueError):
                DIAG['qualify_gpu'](profile, '0x1002', '0x' + pci, FLEET['GPU_UNLOCK_IDS'])

    def test_pinned_firmware_inventory_has_all_families_and_no_external_payload(self):
        files = json.loads((ROOT / 'inputs/firmware-output.json').read_text())['files']
        self.assertEqual(len([name for name in files if name.endswith('.bin')]), 16)
        self.assertEqual(set(name for name in files if not name.endswith('.bin')), {'NVIDIA-LICENSE'})
        for chip in ('ga100', 'ga102', 'ad102', 'tu102'):
            for prefix in ('booter_load_', 'gsp_boot_', 'gsp_boot_desc_'):
                self.assertIn(prefix + chip + '.bin', files)
        self.assertFalse(any('v67' in name for name in files))
