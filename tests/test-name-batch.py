"""Batch display-name editing preserves host authorization and source files."""
import copy
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CORE = runpy.run_path(str(ROOT / 'toolkit/fleet.py'))
GUI = runpy.run_path(str(ROOT / 'gui/fedora-gui.py'))
WORKER = runpy.run_path(str(ROOT / 'gui/fedora-worker.py'))
FIXTURE = runpy.run_path(str(ROOT / 'tests/test-windows-fleet.py'))
fleet = CORE['fleet']


def registry():
    a, b = FIXTURE['host'](test=True), FIXTURE['host'](test=True)
    b['hardware_uuid'] = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
    a['windows_computer_name'] = 'OLD-A'
    return fleet.merge([a, b], 7)


class NameBatchTests(unittest.TestCase):
    def test_fedora_file_dialogs_distinguish_registry_and_collection(self):
        picker = GUI['pick_json']; calls = []
        with tempfile.TemporaryDirectory() as folder:
            mount = Path(folder)
            (mount / 'data').mkdir(); (mount / 'gpu-config').mkdir()
            with patch.dict(picker.__globals__, {'dialog': lambda *args: calls.append(args) or 'selected.json'}):
                picker(folder, 'registry', registry=True)
                self.assertIn('--filename=' + str(mount / 'gpu-config') + '/', calls[-1])
                picker(folder, 'candidate')
                self.assertIn('--filename=' + str(mount / 'data') + '/', calls[-1])
            outside = mount / 'outside'; outside.mkdir()
            (mount / 'gpu-config').rmdir()
            (mount / 'gpu-config').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                WORKER['new_output']('named.json', mount, 'gpu-config')
            self.assertEqual(list(outside.iterdir()), [])

    def test_one_generation_preserves_mappings_and_source(self):
        value = registry(); before = copy.deepcopy(value)
        updates = [{'hardware_uuid': item['hardware_uuid'], 'windows_computer_name': name}
                   for item, name in zip(value['records'], ('NEW-A', 'NEW-B'))]
        result = CORE['set_computer_names'](value, updates, 8)
        self.assertEqual(result['generation'], 8)
        self.assertEqual([item['windows_computer_name'] for item in result['records']], ['NEW-A', 'NEW-B'])
        self.assertEqual([fleet.mapping(item) for item in result['records']], [fleet.mapping(item) for item in value['records']])
        self.assertEqual(value, before)
        valid = updates[0]
        for invalid in ([], [valid, valid], [valid, {**updates[1], 'windows_computer_name': 'bad\nname'}],
                        [{**valid, 'hardware_uuid': '11111111-2222-3333-4444-555555555555'}],
                        [{**valid, 'gpu_pci_id': '10de:2204'}], [{**valid, 'windows_computer_name': None}]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                CORE['set_computer_names'](value, invalid, 8)
            self.assertEqual(value, before)
        with self.assertRaises(ValueError):
            CORE['set_computer_names'](value, updates, 7)

    def test_fedora_editor_remembers_names_when_returning_to_a_pc(self):
        value = registry()
        a, b = [item['hardware_uuid'] for item in value['records']]
        choices = iter(('edit', 'manual', 'edit', 'manual', 'edit', 'manual', 'save'))
        answers = iter((a, 'EDIT-A', b, 'EDIT-B', a, 'FINAL-A'))
        entries = []
        def dialog(*args):
            if '--entry' in args:
                entries.append(next(arg for arg in args if arg.startswith('--entry-text=')))
            return next(answers)
        request = {'inputs': ['synthetic.json']}
        function = GUI['name_update']
        with patch.dict(function.__globals__, {'invoke': lambda *_: copy.deepcopy(value),
                        'select_id': lambda *_: next(choices), 'dialog': dialog}):
            self.assertEqual(function('/fixture', request), 8)
        self.assertEqual(entries, ['--entry-text=OLD-A', '--entry-text=', '--entry-text=EDIT-A'])
        self.assertEqual(request['name_updates'], [
            {'hardware_uuid': a, 'windows_computer_name': 'FINAL-A'},
            {'hardware_uuid': b, 'windows_computer_name': 'EDIT-B'}])
        self.assertNotIn('host_uuid', request)

    def test_fedora_cancelled_pc_edit_retains_other_drafts(self):
        value = registry(); a, b = [item['hardware_uuid'] for item in value['records']]
        choices = iter(('edit', 'manual', 'edit', 'manual', 'save'))
        answers = iter((a, 'EDIT-A', b, GUI['Cancelled']()))
        def dialog(*_):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer
        request = {'inputs': ['synthetic.json']}
        function = GUI['name_update']
        with patch.dict(function.__globals__, {'invoke': lambda *_: copy.deepcopy(value),
                        'select_id': lambda *_: next(choices), 'dialog': dialog}):
            function('/fixture', request)
        self.assertEqual(request['name_updates'], [{'hardware_uuid': a, 'windows_computer_name': 'EDIT-A'}])

    def test_fedora_editor_cancel_does_not_submit_or_write(self):
        function = GUI['name_update']; request = {'inputs': ['synthetic.json']}
        with patch.dict(function.__globals__, {'invoke': lambda *_: registry(),
                        'select_id': lambda *_: (_ for _ in ()).throw(GUI['Cancelled']())}):
            with self.assertRaises(GUI['Cancelled']):
                function('/fixture', request)
        self.assertEqual(request, {'inputs': ['synthetic.json']})

    def test_fedora_worker_writes_once_after_all_names_validate(self):
        dispatch = WORKER['dispatch']; value = registry()
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as folder:
            mount = Path(folder)
            source = mount / 'source.json'; source.write_bytes(fleet.encode(value))
            original = source.read_bytes()
            updates = [{'hardware_uuid': item['hardware_uuid'], 'windows_computer_name': name}
                       for item, name in zip(value['records'], ('NEW-A', 'NEW-B'))]
            request = {'operation': 'name', 'confirm': 'NAME', 'inputs': [str(source)],
                       'generation': 8, 'output_name': 'named.json', 'name_updates': updates}
            with patch.dict(dispatch.__globals__, {'configuration': lambda *_: mount}):
                response = dispatch(request)
                self.assertEqual(Path(response['output']).parent, mount / 'gpu-config')
                self.assertEqual(fleet.load(Path(response['output'])), CORE['set_computer_names'](value, updates, 8))
                invalid = {**request, 'output_name': 'invalid.json',
                           'name_updates': [updates[0], {**updates[1], 'windows_computer_name': 'bad\nname'}]}
                with self.assertRaises(ValueError):
                    dispatch(invalid)
                self.assertFalse((mount / 'gpu-config/invalid.json').exists())
                with self.assertRaises(ValueError):
                    dispatch(request)
                with self.assertRaises(ValueError):
                    dispatch({**request, 'confirm': 'CANCEL'})
            self.assertEqual(source.read_bytes(), original)
