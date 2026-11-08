"""Read-only admin evidence plugs into existing Fedora identity/safety checks."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('admin_inventory_fixture', ROOT / 'tests/test-standard-inventory.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class AdminBitLockerTests(unittest.TestCase):
    def test_admin_evidence_replaces_unknown_shell_without_changing_candidate(self):
        candidate = fixture.candidate()
        candidate['bitlocker_observation'] = {'observation': {'raw_code': 0, 'shell_state': 'Unknown'}}
        proof = {**fixture.evidence(), 'kind': 'bitlocker-admin-observation',
                 'source': 'Win32_EncryptableVolume.GetConversionStatus/GetProtectionStatus'}
        result = fixture.tool.complete_candidate(candidate, fixture.HARDWARE,
            (fixture.PARTITION, 1048576, 8 * 1024**3), '0123456789ABCDEF', True, proof, now=fixture.NOW)
        self.assertEqual(result['bitlocker_protection'], 'Off')
        self.assertEqual(candidate['bitlocker_observation']['observation']['shell_state'], 'Unknown')
        for changes in ({'ntfs_uuid':'FEDCBA9876543210'}, {'hardware_uuid':'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'},
                        {'bitlocker_protection':'Unknown'}, {'bitlocker_volume_status':'EncryptedOrInProgress'},
                        {'checked_at_unix':0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                fixture.tool.complete_candidate(candidate, fixture.HARDWARE,
                    (fixture.PARTITION, 1048576, 8 * 1024**3), '0123456789ABCDEF', True,
                    {**proof, **changes}, now=fixture.NOW)

    @unittest.skipUnless(shutil.which('pwsh'), 'Optional PowerShell needed for mocked Windows API execution')
    def test_collector_rejects_unknown_encrypted_failed_and_existing_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'proof.json'
            command = [shutil.which('pwsh'), '-NoProfile', '-NonInteractive', '-File',
                       str(ROOT / 'tests/admin-bitlocker-fixture.ps1'),
                       '-ScriptPath', str(ROOT / 'toolkit/save-admin-bitlocker.ps1'), '-OutputPath', str(output)]
            for scenario in ('missing','encrypted','transition','nonzero','protected','unknown','null','failed','serial-error'):
                with self.subTest(scenario=scenario):
                    result = subprocess.run(command + ['-Scenario', scenario], capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(output.exists())
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(output.read_text())
            self.assertEqual(value['ntfs_uuid'], '0123456789ABCDEF')
            before = output.read_bytes()
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_bytes(), before)
