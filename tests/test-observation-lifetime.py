"""Old observations remain usable; identities and malformed timestamps are checked."""
import json
from pathlib import Path
import runpy
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
fleet_fixture = runpy.run_path(str(ROOT / 'tests/test-fleet-registry.py'))
standard_fixture = runpy.run_path(str(ROOT / 'tests/test-standard-inventory.py'))
fleet = fleet_fixture['fleet']
tool = standard_fixture['tool']
boot = fleet_fixture['root']


class ObservationLifetimeTests(unittest.TestCase):
    def test_old_registry_can_be_loaded_refreshed_and_deployed(self):
        record = fleet_fixture['host']()
        record['checked_at_unix'] = 1000
        record['fast_startup_enabled'] = True
        old = fleet.merge([record], 1, '0.9.1-shared')
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / 'usb-config.json').write_text(json.dumps({
                'schema': 1, 'kind': 'gpu-linux-config', 'root_version': '0.9.1-shared'}))
            fleet.deploy(old, target)
            current = target / 'gpu-config/registry.json'
            self.assertEqual(fleet.load(current)['records'][0]['checked_at_unix'], 1000)
            refreshed = {**fleet_fixture['host'](), 'fast_startup_enabled': True}
            new = fleet.merge([old, refreshed], 2, '0.9.1-shared')
            fleet.deploy(new, target)
            self.assertGreater(fleet.load(current)['records'][0]['checked_at_unix'], 1000)

    def test_old_standard_candidate_and_admin_or_shell_evidence_are_accepted(self):
        candidate = standard_fixture['candidate']()
        candidate['checked_at_unix'] = 1000
        candidate['fast_startup_enabled'] = True
        admin = standard_fixture['evidence']()
        admin['checked_at_unix'] = 900
        shell = standard_fixture['ShellRegistrationTests']().shell()
        shell['checked_at_unix'] = 900
        for evidence in (admin, shell):
            with self.subTest(kind=evidence.get('kind', 'admin')):
                value = tool.complete_candidate(candidate, standard_fixture['HARDWARE'],
                    (standard_fixture['PARTITION'], 1048576, 8 * 1024**3),
                    '0123456789ABCDEF', True, evidence, now=standard_fixture['NOW'])
                self.assertEqual(value['checked_at_unix'], 900)

    def test_legacy_boot_and_build_accept_old_observations(self):
        record = fleet_fixture['host']()
        record['checked_at_unix'] = 1000
        record['fast_startup_enabled'] = True
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            hosts = root / 'gpu-linux/hosts'
            hosts.mkdir(parents=True)
            (hosts / (record['hardware_uuid'] + '.json')).write_text(json.dumps(record))
            self.assertEqual(boot.host_profile(root, record['hardware_uuid'])['checked_at_unix'], 1000)
            check = runpy.run_path(str(ROOT / 'scripts/check-host-inventory.py'))['inspect_directory']
            result = check(hosts, {record['hardware_uuid']: record['gpu_bdf']}, signed_boot=True)
            self.assertEqual(result[0]['checked_at_unix'], 1000)

    def test_invalid_or_future_timestamps_still_fail(self):
        for value in (None, True, '1000', -1, float('nan'), float('inf'), 2001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                fleet.record({**fleet_fixture['host'](), 'checked_at_unix': value}, now=2000)


if __name__ == '__main__':
    unittest.main()
