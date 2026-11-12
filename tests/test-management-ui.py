"""Device-free checks for management operation flows, schema migration and labels."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
UI = runpy.run_path(str(ROOT / 'gui/ui-model.py'))
FEDORA = runpy.run_path(str(ROOT / 'gui/fedora-gui.py'))
fixture = runpy.run_path(str(ROOT / 'tests/test-fleet-registry.py'))
fleet = fixture['fleet']


class ManagementUiTests(unittest.TestCase):
    def test_locale_selection_and_bilingual_messages(self):
        for env, expected in (({}, 'en'), ({'LANG': 'ja_JP.UTF-8'}, 'ja'),
                              ({'LANG': 'en_US.UTF-8'}, 'en'), ({'LANG': 'fr_FR.UTF-8'}, 'en'),
                              ({'LANG': 'ja_JP.UTF-8', 'LC_ALL': 'C'}, 'en'),
                              ({'LANG': 'en_US.UTF-8', 'LC_MESSAGES': 'ja_JP.UTF-8'}, 'ja'),
                              ({'LANG': 'en_US.UTF-8', 'LANGUAGE': 'ja:en'}, 'ja')):
            with self.subTest(env=env):
                self.assertEqual(UI['language'](env), expected)
        for key, value in UI['MESSAGES'].items():
            self.assertEqual(set(value), {'ja', 'en'}, key)
            self.assertTrue(value['en'] and value['ja'], key)
        # Windows PowerShell JSON object keys are case insensitive.
        keys = [key.casefold() for key in UI['MESSAGES']]
        self.assertEqual(len(keys), len(set(keys)))

    def test_each_operation_has_only_its_own_fields(self):
        windows = {op['id']: op for op in UI['operations']('windows')}
        fedora = {op['id']: op for op in UI['operations']('fedora')}
        self.assertEqual(windows['Status']['fields'], ['files'])
        self.assertEqual(windows['Collect']['fields'], ['usb', 'output'])
        self.assertEqual(windows['Merge']['fields'], ['files', 'generation', 'output'])
        self.assertEqual(windows['AdminBitLocker']['fields'], ['drive', 'output'])
        self.assertNotIn('gpu', fedora['register-test']['fields'])
        self.assertEqual(fedora['Status']['fields'], [])
        for platform in ('windows', 'fedora'):
            for operation in UI['operations'](platform):
                for key in (operation['category'], operation['label'], operation['description'], *operation['fields']):
                    self.assertIn(key, UI['MESSAGES'])

    def test_registry_rows_show_names_with_legacy_fallback(self):
        value = fleet.merge([fixture['host']()], 1, 'fixture')
        self.assertEqual(UI['pc_rows'](value, 'ja')[0][0], 'PC名未登録')
        value = fleet.set_computer_name(value, value['records'][0]['hardware_uuid'], '開発PC-01', 2)
        self.assertEqual(UI['pc_rows'](value, 'en')[0][0], '開発PC-01')

    def test_pc_labels_are_separated_from_zenity_options(self):
        choose = FEDORA['choose']
        calls = []
        with patch.dict(choose.__globals__, {'dialog': lambda *args: calls.append(args) or '--help'}):
            self.assertEqual(choose('PC', ['--help']), '--help')
        self.assertEqual(calls[0][-2:], ('--', '--help'))

    def test_fedora_cancel_at_category_performs_no_operation(self):
        main = FEDORA['main']
        with patch.dict(main.__globals__, {'select_id': lambda *_: (_ for _ in ()).throw(FEDORA['Cancelled']())}), \
             patch('shutil.which', return_value='/fixture/zenity'), \
             patch.dict(main.__globals__, {'invoke': lambda *_: self.fail('Cancellation must not invoke a worker')}):
            main()

    def test_fedora_test_registration_never_requests_gpu_selection(self):
        main = FEDORA['main']
        selections = iter(('registration', 'register-test', 'embedded'))
        requests = []
        def select(*_):
            try:
                return next(selections)
            except StopIteration:
                raise FEDORA['Cancelled']()
        def invoke(request):
            requests.append(request.copy())
            return {'review_digest': 'a' * 64}
        replacements = {'select_id': select, 'opened_config': lambda: '/fixture',
                        'pick_json': lambda *_: '/fixture/candidate.json', 'invoke': invoke,
                        'show': lambda *_: None, 'confirm': lambda *_: None,
                        'gpu_choices': lambda: self.fail('TEST must not request a GPU')}
        with patch.dict(main.__globals__, replacements), patch('shutil.which', return_value='/fixture/zenity'):
            main()
        self.assertEqual([r['operation'] for r in requests], ['registration-preview', 'register'])
        self.assertTrue(all(r['no_gpu_test'] is True and r['gpu_bdf'] is None for r in requests))


class RegistryNamesTests(unittest.TestCase):
    def legacy(self):
        return {'schema': 1, 'generation': 7, 'root_version': '0.9.2', 'records': [fixture['host']()]}

    def test_legacy_conversion_and_name_updates_preserve_all_security_mappings(self):
        original = self.legacy()
        encoded = fleet.encode(original)
        converted = fleet.convert(fleet.decode(encoded), 8)
        self.assertEqual(converted['schema'], 2)
        self.assertIsNone(converted['records'][0]['windows_computer_name'])
        host = original['records'][0]['hardware_uuid']
        renamed = fleet.set_computer_name(converted, host, '推論PC-01', 9)
        self.assertEqual(renamed['records'][0]['windows_computer_name'], '推論PC-01')
        self.assertEqual(fleet.mapping(renamed['records'][0]), fleet.mapping(original['records'][0]))
        self.assertEqual(fleet.encode(original), encoded)
        with self.assertRaises(ValueError):
            fleet.convert(original, 7)
        with self.assertRaises(ValueError):
            fleet.set_computer_name(converted, 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'OTHER-PC', 9)

    def test_bad_names_are_rejected_and_same_name_different_pcs_are_allowed(self):
        for name in ('', ' bad', 'bad ', 'x\ny', 'x\x00y', 'x\u202ey', 'x' * 256, 4, True, 'x\u2028y'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                fleet.computer_name(name)
        first = {**fixture['host'](), 'windows_computer_name': 'LLM-PC'}
        second = {**first, 'hardware_uuid': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'}
        self.assertEqual(len(fleet.merge([first, second], 1)['records']), 2)
        with self.assertRaisesRegex(ValueError, 'Conflicting PC names'):
            fleet.merge([first, {**first, 'windows_computer_name': 'NEW-PC'}], 1)

    def test_missing_names_and_envelope_schema_mismatch_are_rejected_for_schema2(self):
        value = {**self.legacy(), 'schema': 2}
        with self.assertRaisesRegex(ValueError, 'name field'):
            fleet.decode(fleet.encode(value))
        envelope = json.loads(fleet.encode(self.legacy()))
        envelope['schema'] = 2
        with self.assertRaisesRegex(ValueError, 'schema'):
            fleet.decode(json.dumps(envelope).encode())

    def test_old_usb_is_protected_and_new_usb_allows_only_label_changes_at_new_generation(self):
        old = self.legacy()
        new = fleet.set_computer_name(old, old['records'][0]['hardware_uuid'], 'LLM-PC', 8)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            marker = {'schema': 1, 'kind': 'gpu-linux-config', 'root_version': '0.9.2'}
            (target / 'usb-config.json').write_text(json.dumps(marker))
            fleet.deploy(old, target)
            before = (target / 'gpu-config/registry.json').read_bytes()
            with self.assertRaisesRegex(ValueError, 'update the USB'):
                fleet.deploy(new, target)
            self.assertEqual((target / 'gpu-config/registry.json').read_bytes(), before)
            (target / 'usb-config.json').write_text(json.dumps({**marker, 'registry_schema': 2}))
            fleet.deploy(new, target)
            self.assertEqual(fleet.load(target / 'gpu-config/registry.json')['records'][0]['windows_computer_name'], 'LLM-PC')
            for field, value in (('storage_directory', 'Other'), ('gpu_bdf', '0000:02:00.0')):
                changed = copy.deepcopy(new)
                changed['generation'] += 1
                changed['records'][0][field] = value
                with self.subTest(field=field), self.assertRaises(ValueError):
                    fleet.deploy(changed, target)


if __name__ == '__main__':
    unittest.main()
