"""Synthetic trust, archive and build entry checks; no real signing keys."""
import io
import json
import os
from pathlib import Path
import runpy
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = runpy.run_path(str(ROOT / 'scripts/public-inputs.py'))
FEDORA = runpy.run_path(str(ROOT / 'scripts/verify-fedora-input.py'))
REPO = runpy.run_path(str(ROOT / 'scripts/verify-repository-inputs.py'))
FIRMWARE = runpy.run_path(str(ROOT / 'scripts/prepare-firmware.py'))
EFI = runpy.run_path(str(ROOT / 'scripts/prepare-signed-efi.py'))
BUILD = runpy.run_path(str(ROOT / 'scripts/build-usb.py'))
SIGN = runpy.run_path(str(ROOT / 'scripts/sign-gpu-modules.py'))


class PublicInputTests(unittest.TestCase):
    def test_primary_key_parser_separates_signing_subkeys(self):
        text = 'pub:::::::::\nfpr:::::::::PRIMARY:\nsub:::::::::\nfpr:::::::::SUBKEY:\n'
        self.assertEqual(PUBLIC['primary_fingerprints'](text), ['PRIMARY'])
        with self.assertRaisesRegex(ValueError, 'public keys'):
            PUBLIC['primary_fingerprints']('sec:::::::::\nfpr:::::::::SECRET:\n')

    def test_fedora_checksum_requires_unique_exact_filename_and_hash(self):
        sha = '1' * 64
        line = f'SHA256 (fixture.iso) = {sha}\n'.encode()
        FEDORA['checksum_matches'](line, 'fixture.iso', sha)
        for data, filename, digest in ((line + line, 'fixture.iso', sha),
                                       (line, 'other.iso', sha), (line, 'fixture.iso', '2' * 64)):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                FEDORA['checksum_matches'](data, filename, digest)

    def test_repository_metadata_rejects_duplicate_fields(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            REPO['fields']('Package: fixture\nSHA256: a\nSHA256: b\n')

    def test_package_authentication_checks_version_hash_size_arch_and_path(self):
        entry = {'package': 'fixture', 'version': '1', 'sha256': 'a' * 64,
                 'size': 10, 'architecture': 'amd64', 'url': 'https://deb.debian.org/debian/pool/main/f/fixture.deb'}
        candidate = {'Package': 'fixture', 'Version': '1', 'SHA256': 'a' * 64,
                     'Size': '10', 'Architecture': 'amd64', 'Filename': 'pool/main/f/fixture.deb'}
        self.assertTrue(REPO['package_matches'](entry, candidate))
        for field in ('Package', 'Version', 'SHA256', 'Size', 'Architecture', 'Filename'):
            with self.subTest(field=field):
                self.assertFalse(REPO['package_matches'](entry, {**candidate, field: 'wrong'}))

    def test_firmware_reads_only_regular_unique_expected_members(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.tar.xz'
            for kind in ('regular', 'duplicate', 'link', 'missing'):
                with tarfile.open(path, 'w:xz') as archive:
                    for _ in range(2 if kind == 'duplicate' else 1):
                        member = tarfile.TarInfo('source.c' if kind != 'missing' else 'other.c')
                        member.size = 3
                        if kind == 'link':
                            member.type, member.linkname, member.size = tarfile.SYMTYPE, '/outside', 0
                        archive.addfile(member, io.BytesIO(b'abc'))
                if kind == 'regular':
                    self.assertEqual(FIRMWARE['read_source_members'](path, {'source.c'}), {'source.c': b'abc'})
                else:
                    with self.subTest(kind=kind), self.assertRaises(ValueError):
                        FIRMWARE['read_source_members'](path, {'source.c'})

    def test_pe_certificate_table_structure_is_required(self):
        data = bytearray(1024)
        data[:2] = b'MZ'
        struct.pack_into('<I', data, 0x3c, 64)
        data[64:68] = b'PE\0\0'
        struct.pack_into('<H', data, 88, 0x20b)
        with self.assertRaisesRegex(ValueError, 'certificate table'):
            EFI['check_pe_certificate'](data)
        struct.pack_into('<II', data, 88 + 112 + 32, 512, 128)
        struct.pack_into('<IHH', data, 512, 120, 0x200, 2)
        EFI['check_pe_certificate'](data)
        struct.pack_into('<H', data, 518, 1)
        with self.assertRaisesRegex(ValueError, 'Authenticode'):
            EFI['check_pe_certificate'](data)

    def test_build_plan_never_invokes_owner_key_creation_or_module_install(self):
        commands = BUILD['prepare_commands']()
        joined = '\n'.join(' '.join(command) for command in commands)
        self.assertIn('verify-repository-inputs.py', joined)
        self.assertIn('prepare-firmware.py', joined)
        self.assertNotIn('create-signing-key.py', joined)
        self.assertNotIn('sign-gpu-modules.py', joined)
        self.assertNotIn('modprobe', joined)

    def test_build_prepare_refuses_existing_outputs_before_subprocess(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'build/debian-root').mkdir(parents=True)
            fresh = FIRMWARE['fresh_output']
            with (patch.dict(fresh.__globals__, ROOT=root),
                  patch.dict(BUILD['main'].__globals__, ROOT=root, check_host=lambda: None),
                  patch('runpy.run_path', return_value={'fresh_output': fresh}),
                  patch('sys.argv', ['build-usb.py', 'prepare']), patch('subprocess.run') as run):
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    BUILD['main']()
                run.assert_not_called()

    def test_all_new_input_locks_are_resolved_without_network(self):
        fetch = runpy.run_path(str(ROOT / 'scripts/fetch-inputs.py'))
        records = fetch['input_plan'](list(fetch['GROUPS']))
        self.assertEqual(len(records), len({record['file'] for record in records}))
        for record in records:
            self.assertTrue(record['file'].startswith('.cache/'))

    def test_owner_signing_prompt_requires_terminal_and_clears_pin_on_failure(self):
        with patch('sys.stdin.isatty', return_value=False):
            with self.assertRaisesRegex(ValueError, 'interactive'):
                SIGN['with_signing_pin'](lambda: None, reader=lambda prompt: 'synthetic-pin')
        with (patch('sys.stdin.isatty', return_value=True), patch('sys.stderr.isatty', return_value=True),
              patch.dict(os.environ, {'KBUILD_SIGN_PIN': 'prior-synthetic-value'})):
            def failing_operation():
                self.assertEqual(os.environ['KBUILD_SIGN_PIN'], 'new-synthetic-value')
                raise ValueError('synthetic signing failure')
            with self.assertRaisesRegex(ValueError, 'synthetic signing failure'):
                SIGN['with_signing_pin'](failing_operation, reader=lambda prompt: 'new-synthetic-value')
            self.assertEqual(os.environ['KBUILD_SIGN_PIN'], 'prior-synthetic-value')
