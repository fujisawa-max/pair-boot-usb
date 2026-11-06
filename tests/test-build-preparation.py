"""Synthetic checks for build preparation and owner-run key storage boundaries."""
import json
from pathlib import Path
import runpy
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch
import subprocess

ROOT = Path(__file__).resolve().parents[1]
KEYS = runpy.run_path(str(ROOT / 'scripts/create-signing-key.py'))
DEBIAN = runpy.run_path(str(ROOT / 'scripts/prepare-debian-build.py'))
UPSTREAM = runpy.run_path(str(ROOT / 'scripts/prepare-upstream.py'))
VALIDATE = runpy.run_path(str(ROOT / 'scripts/validate-appliance.py'))
SHARED = runpy.run_path(str(ROOT / 'scripts/prepare-shared-usb-tools.py'))


class PreparationTests(unittest.TestCase):
    def test_key_creation_uses_encrypted_default_and_private_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'new-key-store'
            runner = Mock()
            KEYS['create'](target, runner=runner)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            command = runner.call_args.args[0]
            self.assertEqual(command[:5], ['openssl', 'req', '-new', '-x509', '-newkey'])
            for forbidden in ('-nodes', '-noenc', '-passout', '-passin'):
                self.assertNotIn(forbidden, command)
            self.assertIn('rsa:3072', command)
            self.assertEqual(command[command.index('-outform') + 1], 'DER')
            # Mocked OpenSSL: no real private key is generated in this test.
            self.assertEqual(list(target.iterdir()), [])

    def test_key_storage_rejects_repo_existing_and_symlink_paths(self):
        with self.assertRaises(ValueError):
            KEYS['key_directory'](ROOT / 'build/prohibited-key')
        with self.assertRaises(ValueError):
            KEYS['key_directory']('/dev/prohibited-key')
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            with self.assertRaises(ValueError):
                KEYS['key_directory'](base)
            (base / 'link').symlink_to(base, target_is_directory=True)
            with self.assertRaises(ValueError):
                KEYS['key_directory'](base / 'link/new-key')

    def test_failed_key_creation_preserves_directory_and_refuses_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'partial'
            with self.assertRaises(OSError):
                KEYS['create'](target, runner=Mock(side_effect=OSError('synthetic failure')))
            self.assertTrue(target.is_dir())
            with self.assertRaises(ValueError):
                KEYS['create'](target, runner=Mock())

    def test_extra_tools_are_pinned_and_have_known_provenance(self):
        entries = json.loads((ROOT / 'inputs/debian-build-tools-inputs.json').read_text())['packages']
        known = {}
        for name in ('debian-runtime-inputs.json', 'nvidia-driver-inputs.json'):
            for entry in json.loads((ROOT / 'inputs' / name).read_text())['packages']:
                known[entry['filename']] = entry
        self.assertEqual({entry['package'] for entry in entries},
                         {'g++-14-x86-64-linux-gnu', 'libstdc++-14-dev', 'xz-utils', 'hostname'})
        for entry in entries:
            self.assertEqual(entry, known[entry['filename']])

    def test_debian_output_rejects_links_and_existing_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'build').mkdir()
            (root / 'build/existing').mkdir()
            (root / 'build/link').symlink_to(root / 'build/existing', target_is_directory=True)
            with patch.dict(DEBIAN['output_path'].__globals__, ROOT=root):
                for target in (root / 'build', root / 'other', root / 'build/existing', root / 'build/link/new'):
                    with self.subTest(target=target), self.assertRaises(ValueError):
                        DEBIAN['output_path'](target)
                self.assertEqual(DEBIAN['output_path'](root / 'build/new'), root / 'build/new')

    def test_upstream_wrong_commit_or_dirty_checkout_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with patch.dict(UPSTREAM['verify'].__globals__, git=Mock(return_value='wrong')):
                with self.assertRaisesRegex(ValueError, 'wrong commit'):
                    UPSTREAM['verify'](directory, 'expected')
            with patch.dict(UPSTREAM['verify'].__globals__, git=Mock(side_effect=['expected', ' M modified.c'])):
                with self.assertRaisesRegex(ValueError, 'local changes'):
                    UPSTREAM['verify'](directory, 'expected')

    def test_empty_host_inventory_only_permitted_for_explicit_shared_registry(self):
        with tempfile.TemporaryDirectory() as folder:
            tree = Path(folder)
            (tree / 'gpu-linux').mkdir()
            marker = tree / 'gpu-linux/master.json'
            for flag in (False, 'true', None):
                marker.write_text(json.dumps({'shared_registry': flag}))
                with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, 'Missing reviewed host'):
                    VALIDATE['check_host_gpu_addresses'](tree)
            marker.write_text(json.dumps({'shared_registry': True}))
            VALIDATE['check_host_gpu_addresses'](tree)
            hosts = tree / 'gpu-linux/hosts'
            hosts.mkdir()
            (hosts / 'fixture.json').write_text(json.dumps({'gpu_bdf': 'invalid'}))
            with self.assertRaisesRegex(ValueError, 'GPU PCI address'):
                VALIDATE['check_host_gpu_addresses'](tree)

    def test_shared_media_rejects_bad_public_certificate_before_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'build/fedora'
            (source / 'boot/x86_64/loader').mkdir(parents=True)
            (source / 'boot/x86_64/loader/initrd').write_bytes(b'synthetic initrd')
            (source / 'fedora-manifest.json').write_text('{}')
            cert = root / 'public-fixture.der'
            cert.write_bytes(b'invalid public fixture; no private key')
            output, fedora = root / 'build/tools', root / 'build/fedora-tools'
            arguments = ['prepare-shared-usb-tools.py', '--fedora-source', str(source),
                         '--certificate', str(cert), '--output', str(output), '--fedora-output', str(fedora)]
            with (patch.dict(SHARED['main'].__globals__, ROOT=root),
                  patch('sys.argv', arguments),
                  patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, ['openssl']))):
                with self.assertRaises(subprocess.CalledProcessError):
                    SHARED['main']()
            self.assertFalse(output.exists())
            self.assertFalse(fedora.exists())

    def test_shared_media_output_refuses_symlink_alias(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'build').mkdir()
            (root / 'build/link').symlink_to(root / 'build/new')
            with patch.dict(SHARED['fresh_output'].__globals__, ROOT=root):
                with self.assertRaisesRegex(ValueError, 'symlink'):
                    SHARED['fresh_output'](root / 'build/link')
