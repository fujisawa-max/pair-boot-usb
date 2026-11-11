"""Registry schema and generations stay independent of release labels."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
fixture = runpy.run_path(str(ROOT / 'tests/test-fleet-registry.py'))
fleet, boot = fixture['fleet'], fixture['root']


class RegistrySchemaTests(unittest.TestCase):
    def test_deploy_same_generation_across_releases_preserves_records_and_generates_hosts(self):
        value = fleet.merge([fixture['host']()], 7, '0.9.0-shared')
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / 'usb-config.json').write_text(json.dumps({
                'schema': 1, 'kind': 'gpu-linux-config', 'root_version': '0.9.2-shared'}))
            fleet.deploy(value, target)
            original = fleet.load(target / 'gpu-config/registry.json')
            self.assertEqual(original, value)
            relabelled = {**value, 'root_version': '0.9.2-shared'}
            fleet.deploy(relabelled, target)
            current = fleet.load(target / 'gpu-config/registry.json')
            self.assertEqual(current['generation'], 7)
            self.assertEqual(current['records'], original['records'])
            self.assertTrue((target / 'gpu-config/hosts' / (value['records'][0]['hardware_uuid'] + '.env')).is_file())
            with self.assertRaisesRegex(ValueError, 'rollback'):
                fleet.deploy({**value, 'generation': 6}, target)

    def test_host_addition_changes_generation_but_preserves_schema(self):
        first = fleet.merge([fixture['host']()], 7, '0.9.1-shared')
        other = {**fixture['host'](), 'hardware_uuid': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'}
        updated = fleet.merge([first, other], 8, '0.9.2-shared')
        self.assertEqual(updated['schema'], first['schema'])
        self.assertEqual(updated['generation'], 8)
        self.assertEqual(len(updated['records']), 2)
        self.assertEqual(fleet.for_version(updated, '0.9.3-shared'), updated)

    def test_unknown_schema_and_unsafe_marker_are_rejected(self):
        value = fleet.merge([fixture['host']()], 1, 'origin')
        for schema in (2, True, '1', None):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                fleet.decode(fleet.encode({**value, 'schema': schema}))
        for marker in ({'schema': 2, 'kind': 'gpu-linux-config', 'root_version': 'origin'},
                       {'schema': 1, 'kind': 'other', 'root_version': 'origin'},
                       {'schema': 1, 'kind': 'gpu-linux-config', 'root_version': None}):
            with self.subTest(marker=marker), self.assertRaises(ValueError):
                fleet.configuration_marker(marker)

    def test_boot_reads_registered_host_when_media_registry_and_root_releases_differ(self):
        value = fleet.merge([fixture['host']()], 7, '0.9.0-shared')
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            config = state / 'config'
            config.mkdir()
            (config / 'usb-config.json').write_text(json.dumps({
                'schema': 1, 'kind': 'gpu-linux-config', 'root_version': '0.9.1-shared'}))
            (config / 'gpu-config').mkdir()
            (config / 'gpu-config/registry.json').write_bytes(fleet.encode(value))
            actual_spec = importlib.util.spec_from_file_location
            def spec(name, path, *args, **kwargs):
                if str(path) == '/usr/lib/fleet-registry.py':
                    path = ROOT / 'runtime/fleet-registry.py'
                return actual_spec(name, path, *args, **kwargs)
            with patch('importlib.util.spec_from_file_location', side_effect=spec), \
                 patch.object(boot, 'configuration_device', return_value='/dev/fixture-config'), \
                 patch.object(boot, 'run', return_value=''), redirect_stdout(io.StringIO()):
                record = boot.shared_host_profile('/dev/fixture-master', {'version': '0.9.2-shared'},
                                                  value['records'][0]['hardware_uuid'], state)
            self.assertEqual(record, value['records'][0])


if __name__ == '__main__':
    unittest.main()
