"""Revision direction, explicit downgrade and failure recovery on ordinary files."""
import importlib.util
import json
import io
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('revision_api', ROOT / 'runtime/gpu-root.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
upgrade = runpy.run_path(str(ROOT / 'runtime/replace-root.py'))
policy = runpy.run_path(str(ROOT / 'runtime/release-info.py'))
KERNEL = 'fixture-kernel'


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.target = self.parent / 'root.img'
        self.master = self.parent / 'master.img'
        self.state = self.parent / 'state.img'
        self.state.write_bytes(b'retained fixture settings')

    def prepare(self, old=1, new=2, kernel=KERNEL, abi=1):
        def image(path, info):
            tree = self.parent / ('tree-' + path.stem)
            location = tree / 'usr/lib/gpu-root/image.json'
            location.parent.mkdir(parents=True)
            location.write_text(json.dumps(info))
            with path.open('wb') as output:
                output.truncate(32 * 1024**2)
            subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-F', '-d', str(tree), str(path)], check=True, capture_output=True)
        old_info = {'version': '0.9.1-shared', 'state_schema': 1, 'kernel_release': kernel,
                    'root_revision': old, 'boot_abi': abi}
        if old == 0:
            old_info['version'] = '0.9.0-shared'
            del old_info['root_revision'], old_info['boot_abi']
        image(self.target, old_info)
        new_info = {'version': '0.9.1-shared', 'state_schema': 1, 'kernel_release': KERNEL,
                    'root_revision': new, 'boot_abi': 1}
        image(self.master, new_info)
        self.receipt = {'schema': 1, 'version': old_info['version'], 'state_schema': 1,
                        'size': self.target.stat().st_size, 'original_sha256': api.digest(self.target)}
        if old:
            self.receipt.update(root_revision=old, boot_abi=abi)
        (self.parent / 'root-image.json').write_text(json.dumps(self.receipt))
        self.manifest = {**new_info, 'persistent_state': {'schema': 1},
                         'size': self.master.stat().st_size, 'sha256': api.digest(self.master)}

    def run_replace(self, **kwargs):
        actual = api.run
        def command(*args):
            if args[:2] == ('losetup', '-j'):
                return ''
            self.assertNotIn(args[0], ('mount', 'umount', 'systemctl'))
            return actual(*args)
        with patch.object(api, 'run', side_effect=command):
            return upgrade['replace'](api, self.master, self.target, self.receipt, self.manifest,
                                      free_bytes=kwargs.pop('free_bytes', 2 * 1024**3), **kwargs)

    def test_newer_revision_updates_automatically_and_preserves_old_root_and_state(self):
        self.prepare()
        before = api.digest(self.target)
        self.run_replace(ask=lambda *_: self.fail('Upgrade must not ask'))
        self.assertEqual(api.digest(self.target), self.manifest['sha256'])
        self.assertEqual(api.digest(next(self.parent.glob('root-before-update-*.img'))), before)
        self.assertEqual(self.state.read_bytes(), b'retained fixture settings')
        self.assertEqual(json.loads((self.parent / 'root-image.json').read_text())['root_revision'], 2)

    def test_legacy_0_9_0_is_revision_zero_with_state_and_kernel_checks(self):
        self.prepare(old=0, new=1)
        self.run_replace()
        self.assertEqual(api.digest(self.target), self.manifest['sha256'])
        for value in ({'version': '0.8.0-shared', 'state_schema': 1},
                      {'version': '0.9.1-shared', 'state_schema': 1}, {'root_revision': True}):
            with self.assertRaises(ValueError):
                policy['root_revision'](value)

    def test_equal_revision_equal_hash_keeps_root_without_copy(self):
        self.prepare(old=1, new=1)
        self.manifest['sha256'] = self.receipt['original_sha256']
        self.assertEqual(self.run_replace(), self.target)
        self.assertFalse(list(self.parent.glob('root-before-update-*')))

    def test_equal_revision_changed_hash_stops_without_transactions(self):
        self.prepare(old=1, new=1)
        before = api.digest(self.target)
        with self.assertRaisesRegex(api.SafetyError, 'Same root revision'):
            self.run_replace()
        self.assertEqual(api.digest(self.target), before)
        self.assertFalse(list(self.parent.glob('.root-replace*')))

    def test_older_revision_can_keep_compatible_current_root(self):
        self.prepare(old=2, new=1)
        before = api.digest(self.target)
        choices = []
        def choose(*values):
            choices.append(values)
            return 'keep'
        self.run_replace(choose=choose)
        self.assertEqual(choices, [(2, 1, True, True)])
        self.assertEqual(api.digest(self.target), before)

    def test_confirmed_downgrade_copies_root_but_retains_state(self):
        self.prepare(old=2, new=1)
        self.run_replace(choose=lambda *_: 'replace')
        self.assertEqual(api.digest(self.target), self.manifest['sha256'])
        self.assertEqual(self.state.read_bytes(), b'retained fixture settings')
        self.assertEqual(json.loads((self.parent / 'root-image.json').read_text())['root_revision'], 1)

    def test_incompatible_kernel_disables_keep_and_unknown_action_is_rejected(self):
        self.prepare(old=2, new=1, kernel='other-kernel')
        seen = []
        def choose(*values):
            seen.append(values)
            return 'keep'
        before = api.digest(self.target)
        with self.assertRaises(api.SafetyError):
            self.run_replace(choose=choose)
        self.assertEqual(seen, [(2, 1, False, True)])
        self.assertEqual(api.digest(self.target), before)
        with self.assertRaises(api.SafetyError):
            self.run_replace(choose=lambda *_: 'invented')

    def test_poweroff_leaves_all_images_and_receipts_unchanged(self):
        self.prepare(old=2, new=1)
        before = api.digest(self.target)
        with self.assertRaises(api.RootPoweroff):
            self.run_replace(choose=lambda *_: 'poweroff')
        self.assertEqual(api.digest(self.target), before)
        self.assertFalse(list(self.parent.glob('.root-replace*')))

    def test_bad_hash_abi_receipt_and_low_space_stop_before_mutation(self):
        self.prepare()
        before = api.digest(self.target)
        with patch.dict(self.manifest, sha256='0' * 64), self.assertRaises(api.SafetyError):
            self.run_replace()
        with patch.dict(self.manifest, boot_abi=99), self.assertRaises(api.SafetyError):
            self.run_replace()
        with patch.dict(self.receipt, root_revision=99), self.assertRaises(api.SafetyError):
            self.run_replace()
        with self.assertRaisesRegex(api.SafetyError, 'Insufficient'):
            self.run_replace(free_bytes=0)
        self.assertEqual(api.digest(self.target), before)
        self.assertFalse(list(self.parent.glob('.root-replace*')))

    def test_interrupted_upgrade_retains_original_and_blocks_reprovision(self):
        self.prepare()
        before = api.digest(self.target)
        def fail():
            raise RuntimeError('fixture interruption')
        with self.assertRaises(RuntimeError):
            self.run_replace(after_rename=fail)
        self.assertEqual(api.digest(next(self.parent.glob('root-before-update-*.img'))), before)
        with self.assertRaisesRegex(api.SafetyError, 'Interrupted'):
            api.provision(self.master, self.parent, self.manifest)


class MetadataTests(unittest.TestCase):
    def test_console_downgrade_requires_explicit_confirmation_and_disabled_choices_fail(self):
        for answer, keep, replace, expected in (
                ('1\nDOWNGRADE\n', True, True, 'replace'), ('1\nno\n', True, True, 'poweroff'),
                ('1\nDOWNGRADE\n', True, False, 'poweroff'), ('2\n', True, True, 'keep'),
                ('2\n', False, True, 'poweroff'), ('3\n', True, True, 'poweroff'),
                ('\n', True, True, 'poweroff'), ('', True, True, 'poweroff')):
            with self.subTest(answer=answer, keep=keep, replace=replace):
                class Terminal(io.StringIO):
                    def __init__(self):
                        super().__init__(answer)
                        self.written = []
                    def isatty(self):
                        return True
                    def write(self, text):
                        self.written.append(text)
                        return len(text)
                terminal = Terminal()
                with patch('builtins.open', return_value=terminal):
                    self.assertEqual(upgrade['downgrade_choice'](2, 1, keep, replace), expected)

    def test_keep_compatibility_checks_driver_commit_and_state_types(self):
        info = {'version': '0.9.1-shared', 'root_revision': 1, 'boot_abi': 1,
                'state_schema': 1, 'kernel_release': KERNEL, 'components': {
                    'NVIDIA driver package': 'fixture-driver', 'gpu-unlock commit': 'fixture-commit'}}
        self.assertTrue(policy['compatible'](info, KERNEL, info))
        for changed in ({'state_schema': True}, {'boot_abi': True}, {'boot_abi': 2},
                        {'root_revision': True}, {'kernel_release': 'different'},
                        {'components': {**info['components'], 'NVIDIA driver package': 'other'}},
                        {'components': {**info['components'], 'gpu-unlock commit': 'other'}}):
            with self.subTest(changed=changed):
                self.assertFalse(policy['compatible']({**info, **changed}, KERNEL, info))

    def test_registry_version_conversion_preserves_all_safety_evidence(self):
        fleet = runpy.run_path(str(ROOT / 'runtime/fleet-registry.py'))
        old = fleet['merge']([], 2, '0.9.0-shared')
        new = fleet['for_version'](old, '0.9.1-shared')
        self.assertEqual(new['generation'], 3)
        self.assertEqual(old['records'], new['records'])
        with self.assertRaises(ValueError):
            fleet['for_version'](new, '0.9.0-shared')

    def test_media_information_comes_from_fixed_inputs_and_shared_formatter(self):
        metadata = runpy.run_path(str(ROOT / 'scripts/release-metadata.py'))
        info = metadata['metadata']()
        text = metadata['text']()
        self.assertEqual(info['project_version'], '0.9.1')
        self.assertIn('Root revision: 3', text)
        self.assertIn('Debian 13 trixie', text)
        self.assertIn('44-1.7', text)
        self.assertNotIn(str(Path.home()), text)
        line = policy['banner_line'](info, 'Unlock')
        self.assertIn('Version: 0.9.1', line)
        self.assertIn('Root rev: 3', line)
        self.assertIn('Mode: Unlock', line)
        self.assertLessEqual(len(line), 100)
