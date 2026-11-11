"""Standard-user candidates remain incomplete until independent checks pass."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('standard_fleet', ROOT / 'toolkit/fleet.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
NOW = 10000000
HARDWARE = '01234567-89ab-cdef-0123-456789abcdef'
PARTITION = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'


def candidate():
    return {'schema': 1, 'kind': 'windows-standard-candidate', 'hardware_uuid': HARDWARE,
            'partition_guid': PARTITION, 'partition_offset_bytes': 1048576,
            'partition_size_bytes': 8 * 1024**3, 'volume_size_bytes': 8 * 1024**3 - 4096,
            'filesystem': 'NTFS', 'windows_drive': 'D:', 'partition_is_boot': False, 'partition_is_system': False,
            'partition_is_hidden': False, 'fast_startup_enabled': False,
            'storage_directory': 'GPU-Linux', 'checked_at_unix': NOW - 10}


def evidence():
    return {'schema': 1, 'hardware_uuid': HARDWARE, 'ntfs_uuid': '0123456789ABCDEF',
            'bitlocker_volume_status': 'FullyDecrypted', 'bitlocker_protection': 'Off',
            'checked_at_unix': NOW - 1000}


class StandardInventoryTests(unittest.TestCase):
    def complete(self, value=None, proof=None, partition=None, secure=True):
        value = candidate() if value is None else value
        return tool.complete_candidate(value, HARDWARE,
            (PARTITION, 1048576, 8 * 1024**3) if partition is None else partition,
            '0123456789ABCDEF', secure, evidence() if proof is None else proof, now=NOW)

    def test_partuuid_size_and_proof_age_are_preserved(self):
        value = self.complete()
        self.assertEqual(value['checked_at_unix'], NOW - 1000)
        self.assertTrue(value['secure_boot_enabled'])
        self.assertEqual(value['ntfs_uuid'], '0123456789ABCDEF')
        self.assertEqual(value['partition_size_bytes'] - value['volume_size_bytes'], 4096)
        with self.assertRaisesRegex(ValueError, 'partition size'):
            self.complete(partition=(PARTITION, 1048576, 8 * 1024**3 - 4096))

    def test_unknown_bitlocker_cannot_become_off(self):
        with self.assertRaisesRegex(ValueError, 'BitLocker is unknown'):
            tool.complete_candidate(candidate(), HARDWARE, (PARTITION, 1048576, 8 * 1024**3), '0123456789ABCDEF', True, None, now=NOW)
        for key, value in (('bitlocker_protection', 'Unknown'), ('ntfs_uuid', 'FEDCBA9876543210'), ('checked_at_unix', -1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.complete(proof={**evidence(), key: value})

    def test_mismatched_pc_guid_and_secure_boot_rejected(self):
        with self.assertRaisesRegex(ValueError, 'different PC'):
            self.complete(value={**candidate(), 'hardware_uuid': '11111111-2222-3333-4444-555555555555'})
        with self.assertRaisesRegex(ValueError, 'PARTUUID'):
            self.complete(partition=('bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee', 1048576, 8 * 1024**3))
        with self.assertRaisesRegex(ValueError, 'Secure Boot'):
            self.complete(secure=False)

    def test_efi_variables_enabled_setup_and_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            suffix = '-8be4df61-93ca-11d2-aa0d-00e098032b8c'
            (root / ('SecureBoot' + suffix)).write_bytes(b'\x07\0\0\0\x01')
            (root / ('SetupMode' + suffix)).write_bytes(b'\x07\0\0\0\x00')
            self.assertTrue(tool.secure_boot_state(root))
            (root / ('SetupMode' + suffix)).write_bytes(b'\x07\0\0\0\x01')
            with self.assertRaisesRegex(ValueError, 'SetupMode'):
                tool.secure_boot_state(root)
            (root / ('SetupMode' + suffix)).write_bytes(b'')
            with self.assertRaisesRegex(ValueError, 'Invalid EFI'):
                tool.secure_boot_state(root)

    @unittest.skipUnless((ROOT / 'build/powershell-7.5.4/pwsh').exists(), 'Workspace PowerShell optional')
    def test_export_with_mocked_standard_user_apis(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as folder:
            root = Path(folder)
            output = root / 'candidate.json'
            wrapper = root / 'export-fixture.ps1'
            wrapper.write_text('''$ErrorActionPreference='Stop'
function Get-CimInstance { [pscustomobject]@{UUID='01234567-89ab-cdef-0123-456789abcdef'} }
function Get-Volume { param($DriveLetter) [pscustomobject]@{FileSystem='NTFS';Size=[long]8589930496} }
function Get-Partition { param($DriveLetter) [pscustomobject]@{Guid='{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}';Offset=[long]1048576;Size=[long]8589934592;IsBoot=$false;IsSystem=$false;IsHidden=$false} }
function Get-ItemProperty { param($Path,$Name) [pscustomobject]@{HiberbootEnabled=[int]1} }
& "''' + str(ROOT / 'toolkit/export-standard-inventory.ps1') + '''" -Volume D: -OutputFile "''' + str(output) + '''"
''')
            env = {**os.environ, 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
                   'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
                   'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')}
            result = subprocess.run([str(ROOT / 'build/powershell-7.5.4/pwsh'), '-NoProfile', '-NonInteractive', '-File', str(wrapper)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('候補情報を保存しました', result.stdout)
            self.assertIn(str(output), result.stdout)
            self.assertIn('登録はまだ完了していません', result.stdout)
            self.assertNotIn('Candidate saved.', result.stdout)
            value = json.loads(output.read_text())
            self.assertEqual(value['partition_guid'], PARTITION)
            self.assertEqual(value['bitlocker_protection'], 'Unknown')
            self.assertTrue(value['fast_startup_enabled'])
            self.assertIsNone(value['secure_boot_enabled'])
            tool.check_candidate(value, HARDWARE)
            source = (ROOT / 'toolkit/export-standard-inventory.ps1').read_text()
            for forbidden in ('Win32_EncryptableVolume', 'Confirm-SecureBootUEFI', 'fsutil', 'Get-Disk', 'Start-Process'):
                self.assertNotIn(forbidden, source)


class ShellRegistrationTests(unittest.TestCase):
    def shell(self):
        return {'schema': 1, 'kind': 'bitlocker-shell-observation', 'hardware_uuid': HARDWARE,
                'partition_guid': PARTITION, 'checked_at_unix': NOW - 100,
                'observation': {'volume': 'D:', 'property': 'System.Volume.BitLockerProtection',
                                'raw_code': 2, 'shell_state': 'Off', 'off_candidate': True,
                                'requires_linux_verification': True, 'error_type': None}}

    def complete(self, proof):
        return tool.complete_candidate(candidate(), HARDWARE, (PARTITION, 1048576, 8 * 1024**3),
                                       '0123456789ABCDEF', True, proof, now=NOW)

    def test_shell_off_plus_matched_linux_checks_accepted(self):
        value = self.complete(self.shell())
        self.assertEqual(value['bitlocker_verification_method'], 'windows-shell-off-and-linux-raw-ntfs')
        self.assertEqual(value['checked_at_unix'], NOW - 100)
        formal = tool.fleet.record({**value, 'gpu_bdf': '0000:01:00.0', 'gpu_pci_id': '10de:2504'}, now=NOW)
        self.assertEqual(formal['ntfs_uuid'], '0123456789ABCDEF')

    def test_every_non_off_or_ambiguous_shell_value_refused(self):
        for raw in (0, 1, 3, 4, 5, 6, 7, 8, 99, None, True, '2'):
            proof = self.shell()
            proof['observation']['raw_code'] = raw
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.complete(proof)
        for key, bad in (('shell_state', 'Suspended'), ('off_candidate', False),
                         ('requires_linux_verification', False), ('error_type', 'AccessDenied'),
                         ('volume', 'C:'), ('volume', None), ('property', 'other')):
            proof = self.shell()
            proof['observation'][key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.complete(proof)

    def test_shell_evidence_identity_and_timestamp_are_bound(self):
        for key, bad in (('hardware_uuid', '11111111-2222-3333-4444-555555555555'),
                         ('partition_guid', 'bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee'),
                         ('checked_at_unix', -1), ('checked_at_unix', NOW + 1), ('kind', 'other')):
            proof = self.shell()
            proof[key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.complete(proof)
        proof = self.shell()
        del proof['observation']['error_type']
        with self.assertRaises(ValueError):
            self.complete(proof)

    def test_utf8_bom_shell_file_is_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'shell.json'
            path.write_text(json.dumps(self.shell()), encoding='utf-8-sig')
            self.assertEqual(tool.read_input(path), self.shell())

    @unittest.skipUnless((ROOT / 'build/powershell-7.5.4/pwsh').exists(), 'Workspace PowerShell optional')
    def test_export_embeds_shell_and_saving_needs_no_command_quoting(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as folder:
            env = {**os.environ, 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
                   'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
                   'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')}
            for script in ('export-standard-inventory.ps1', 'save-shell-bitlocker.ps1'):
                output = Path(folder) / (script + '.json')
                result = subprocess.run([str(ROOT / 'build/powershell-7.5.4/pwsh'), '-NoProfile', '-NonInteractive',
                    '-File', str(ROOT / 'tests/standard-windows-api-fixture.ps1'),
                    '-ScriptPath', str(ROOT / 'toolkit' / script), '-OutputPath', str(output)],
                    env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                value = json.loads(output.read_text())
                proof = value['bitlocker_observation'] if script.startswith('export') else value
                self.assertEqual(proof['observation']['raw_code'], 2)
                self.assertEqual(proof['observation']['shell_state'], 'Off')
                self.assertIsNone(proof['observation']['error_type'])
                self.assertEqual(proof['hardware_uuid'], HARDWARE)
                self.assertEqual(proof['partition_guid'], PARTITION)

    def test_preview_then_confirm_writes_registry_and_provenance_only(self):
        from unittest.mock import patch
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            mount = base / 'usb'
            mount.mkdir()
            (mount / 'usb-config.json').write_text(json.dumps({'schema': 1, 'kind': 'gpu-linux-config', 'root_version': '0.7.0-shared'}))
            gpu = base / 'gpu/0000:01:00.0'
            gpu.mkdir(parents=True)
            (gpu / 'vendor').write_text('0x10de')
            (gpu / 'device').write_text('0x2504')
            hardware = base / 'hardware'
            hardware.write_text(HARDWARE)
            inventory = base / 'candidate.json'
            value = {**candidate(), 'bitlocker_observation': self.shell()}
            inventory.write_text(json.dumps(value))
            def mapped(path):
                return {'/sys/class/dmi/id/product_uuid': hardware, '/sys/bus/pci/devices': base / 'gpu'}.get(str(path), Path(path))
            def resolve(item, host, proof):
                return tool.complete_candidate(item, host, (PARTITION, 1048576, 8 * 1024**3), '0123456789ABCDEF', True, proof, now=NOW)
            with patch.object(tool, 'live_config'), patch.object(tool, 'Path', side_effect=mapped), \
                 patch.object(tool, 'resolve_candidate', side_effect=resolve), \
                 patch.object(tool, 'run', side_effect=lambda *args: 'ntfs' if '-p' in args else ('fixture-disk' if 'PKNAME' in args else ('nvme' if 'TRAN' in args else '/dev/fixture'))), \
                 patch.object(tool.time, 'time', return_value=NOW), contextlib.redirect_stdout(io.StringIO()):
                initial = tool.fleet.merge([], 1, '0.7.0-shared')
                tool.fleet.deploy(initial, mount)
                registry = mount / 'gpu-config/registry.json'
                before = registry.read_bytes()
                with self.assertRaisesRegex(ValueError, '--confirm REGISTER'):
                    tool.register(inventory, '0000:01:00.0', mount, '')
                self.assertEqual(registry.read_bytes(), before)
                tool.register(inventory, '0000:01:00.0', mount, 'REGISTER')
                current = tool.fleet.load(registry)
                self.assertEqual(current['generation'], 2)
                self.assertEqual(current['records'][0]['hardware_uuid'], HARDWARE)
                proof = json.loads(next((mount / 'gpu-config/registration-reviews').glob('*.json')).read_text())
                self.assertEqual(proof['method'], 'windows-shell-off-and-linux-raw-ntfs')
                self.assertEqual(proof['windows_candidate']['bitlocker_observation'], self.shell())

    @unittest.skipUnless((ROOT / 'build/powershell-7.5.4/pwsh').exists(), 'Workspace PowerShell optional')
    def test_unknown_fast_startup_observation_is_explained_without_exception_dump(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as folder:
            work = Path(folder)
            output = work / 'not-created.json'
            wrapper = work / 'reject.ps1'
            wrapper.write_text('''
function Get-CimInstance { [pscustomobject]@{UUID='01234567-89ab-cdef-0123-456789abcdef'} }
function Get-Volume { [pscustomobject]@{FileSystem='NTFS';Size=8589930496} }
function Get-Partition { [pscustomobject]@{Guid='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';Offset=1048576;Size=8589934592;IsBoot=$false;IsSystem=$false;IsHidden=$false} }
function Get-ItemProperty { [pscustomobject]@{HiberbootEnabled=2} }
& "''' + str(ROOT / 'toolkit/export-standard-inventory.ps1') + '" -Volume D: -OutputFile "' + str(output) + '"\nexit $LASTEXITCODE\n')
            env = {**os.environ, 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
                   'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
                   'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')}
            result = subprocess.run([str(ROOT / 'build/powershell-7.5.4/pwsh'), '-NoProfile', '-NonInteractive', '-File', str(wrapper)], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('高速スタートアップ', result.stderr)
            self.assertIn('観測情報を保存できません', result.stderr)
            self.assertNotIn('CategoryInfo', result.stderr)
            self.assertFalse(output.exists())
