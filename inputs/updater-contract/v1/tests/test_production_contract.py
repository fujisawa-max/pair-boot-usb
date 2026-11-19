"""Portable metadata conformance suite; no filesystem images or physical devices."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('production_contract', ROOT / 'schemas/production-v1/validate.py')
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)
FIXTURES = ROOT / 'tests/fixtures/production-v1'
BUILD = Path.cwd() / 'build'


class ProductionContractTests(unittest.TestCase):
    def sample(self, name='manifest'):
        return C.load(FIXTURES / (name + '.json'))

    def reject(self, value, kind='manifest'):
        with self.assertRaises(ValueError):
            C.check(value, kind, ROOT)

    def test_locked_contract_and_positive_examples(self):
        C.check_lock(ROOT)
        C.check(C.load(ROOT / C.PROFILE), 'layout', ROOT)
        for kind in ('manifest', 'helper-request', 'helper-receipt'):
            with self.subTest(kind=kind):
                C.check(self.sample(kind), kind, ROOT)
        C.check_exchange_files(*(FIXTURES / (kind + '.json') for kind in
                                ('manifest', 'helper-request', 'helper-receipt')), ROOT)

    def test_unknown_missing_and_boolean_integer(self):
        for kind in ('manifest', 'helper-request', 'helper-receipt'):
            with self.subTest(kind=kind):
                value = self.sample(kind); value['unknown'] = True
                self.reject(value, kind)
                value = self.sample(kind); del value['helper_protocol']
                self.reject(value, kind)
                value = self.sample(kind); value['contract_version'] = True
                self.reject(value, kind)

    def test_wrong_version_layout_and_lock(self):
        for key, change in [('contract_version', 2), ('contract_revision', 2),
                            ('minimum_updater', '0.5.0'), ('contract_lock_sha256', 'f'*64)]:
            value = self.sample(); value[key] = change
            with self.subTest(key=key): self.reject(value)
        value = self.sample(); value['layout']['sha256'] = 'f'*64
        self.reject(value)

    def test_protected_and_unsafe_paths(self):
        for path in ('../fleet.py', '/fleet.py', 'tools/../fleet.py', 'tools/CON.txt',
                     'tools/extra.', 'tools/a:ads', 'tools\\a', 'tools/./a',
                     'gpu-config/registry.json', 'data/a', 'signing-cert.der'):
            value = self.sample(); value['managed_files'][0]['path'] = path
            with self.subTest(path=path): self.reject(value)
        value = self.sample(); value['managed_files'] *= 2
        self.reject(value)
        value = self.sample(); item = copy.deepcopy(value['managed_files'][0])
        item['path'] = 'tools/FLEET.py'; value['managed_files'].append(item)
        self.reject(value)
        value = self.sample(); value['remove_managed_files'] = ['tools/unowned.py']
        self.reject(value)
        for first, second in [('tools/extra', 'tools/extra/file'),
                              ('tools/Extra/a', 'tools/extra/b')]:
            value = self.sample(); item = copy.deepcopy(value['managed_files'][0])
            value['managed_files'][0]['path'] = first
            item['path'] = second; item['payload']['path'] = 'payload/second.py'
            value['managed_files'].append(item)
            self.reject(value)

    def test_downgrade_unsupported_source_and_compatibility(self):
        for key, new in [('boot_abi', 2), ('state_schema', 2), ('certificate_sha256', 'f'*64)]:
            value = self.sample(); value['release'][key] = new
            with self.subTest(key=key): self.reject(value)
        value = self.sample(); value['release']['version'] = value['release']['root_version'] = '0.9.0'
        self.reject(value)
        value = self.sample(); value['accepted_sources'][0]['release']['root_revision'] = 2
        self.reject(value)
        value = self.sample(); value['accepted_sources'][0]['update_sequence'] = 1
        self.reject(value)

    def test_update_sequence(self):
        value = self.sample(); source = value['accepted_sources'][1]
        source.update(kind='updated-media', update_sequence=1, prior_manifest_sha256='e'*64)
        self.reject(value)
        value['update_sequence'] = 2
        C.check(value, 'manifest', ROOT)
        source['prior_manifest_sha256'] = '0'*64
        self.reject(value)

    def test_partition_bounds_roles_and_inventory(self):
        value = self.sample(); value['partitions'][0]['bytes'] += 512
        self.reject(value)
        value = self.sample(); value['partitions'][0]['role'] = 'models'
        self.reject(value)
        value = self.sample(); value['image']['bytes'] = 512
        self.reject(value)
        value = self.sample(); value['logical_inventory'][0]['role'] = 'logs'
        self.reject(value)

    def test_layout_rules_and_reordering(self):
        value = C.load(ROOT / C.PROFILE)
        value['partitions'][0]['start_sector'] = value['partitions'][1]['start_sector']
        self.reject(value, 'layout')
        value = C.load(ROOT / C.PROFILE); value['write_order'].append('models')
        self.reject(value, 'layout')
        value = C.load(ROOT / C.PROFILE); value['partitions'][-1]['operation'] = 'replace'
        self.reject(value, 'layout')
        # Validator resolves roles and geometry from config, not hard-coded partition numbers.
        value = C.load(ROOT / C.PROFILE)
        value['partitions'][0]['number'], value['partitions'][1]['number'] = 2, 1
        C.check(value, 'layout', ROOT)

    def test_selection_snapshot_and_wrong_target(self):
        request = self.sample('helper-request'); request['selected_roles'] = ['master','logs']
        self.reject(request, 'helper-request')
        request = self.sample('helper-request'); request['snapshots'][0]['file']['bytes'] -= 512
        self.reject(request, 'helper-request')
        request = self.sample('helper-request'); request['snapshots'].pop()
        self.reject(request, 'helper-request')
        request = self.sample('helper-request'); receipt = self.sample('helper-receipt')
        receipt['target']['target_token_sha256'] = 'f'*64
        with self.assertRaises(ValueError): C.check_exchange(self.sample(), request, receipt, ROOT)

    def test_registry_migration_and_identity(self):
        value = self.sample('helper-receipt'); value['registry']['after_generation'] += 1
        self.reject(value, 'helper-receipt')
        value = self.sample('helper-receipt'); reg = value['registry']
        reg.update(migration='identity-2', before_schema=2, after_generation=reg['before_generation'],
                   after_sha256=reg['before_sha256'], record_count=0)
        C.check(value, 'helper-receipt', ROOT)
        reg['after_sha256'] = 'f'*64
        self.reject(value, 'helper-receipt')

    def test_raw_hash_binding_and_strict_json(self):
        BUILD.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=BUILD) as tmp:
            tmp = Path(tmp)
            for name in ('manifest','helper-request','helper-receipt'):
                (tmp/(name+'.json')).write_bytes((FIXTURES/(name+'.json')).read_bytes())
            manifest, request, receipt = (tmp/(n+'.json') for n in ('manifest','helper-request','helper-receipt'))
            manifest.write_bytes(manifest.read_bytes() + b'\n')
            with self.assertRaises(ValueError): C.check_exchange_files(manifest, request, receipt, ROOT)
            for raw in (b'{"x":1,"x":2}', b'{"x":NaN}'):
                manifest.write_bytes(raw)
                with self.assertRaises(ValueError): C.load(manifest)

    def test_symlink_input_rejected(self):
        BUILD.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=BUILD) as tmp:
            link = Path(tmp)/'linked.json'
            try: link.symlink_to(FIXTURES/'manifest.json')
            except OSError as exc: self.skipTest('symlink creation unavailable: ' + str(exc))
            with self.assertRaises(ValueError): C.load(link)


if __name__ == '__main__':
    unittest.main()
