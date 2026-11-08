"""State and replacement transactions touch generated files only; no real mounts."""
import hashlib
import importlib.util
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('state_api', ROOT / 'runtime/gpu-root.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
STATE = runpy.run_path(str(ROOT / 'runtime/persistent-state.py'))
REPLACE = runpy.run_path(str(ROOT / 'runtime/replace-root.py'))


class PersistentStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parent = self.root / 'storage'
        self.parent.mkdir()
        self.master = self.root / 'template.img'
        with self.master.open('wb') as stream:
            stream.truncate(64 * 1024**2)
        subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-F', str(self.master)], check=True, capture_output=True)
        self.meta = {'schema': 1, 'filename': 'state.img', 'size': self.master.stat().st_size,
                     'sha256': api.digest(self.master)}
        self.hardware = 'fixture-hardware'

    def create(self, **kwargs):
        return STATE['create'](api, self.master, self.parent, self.meta, self.hardware,
                               free_bytes=2 * 1024**3, **kwargs)

    def test_verified_creation_and_pc_bound_receipt(self):
        target = self.create()
        before = api.digest(target)
        with patch.object(api, 'run', return_value=''):
            result, receipt = STATE['inspect'](api, self.parent, self.hardware)
            self.assertEqual(result, target)
            self.assertEqual(receipt['state_schema'], 1)
            with self.assertRaisesRegex(api.SafetyError, 'mismatch'):
                STATE['inspect'](api, self.parent, 'other-PC')
        self.assertEqual(api.digest(target), before)
        with self.assertRaisesRegex(api.SafetyError, 'exists'):
            self.create()

    def test_corrupt_master_low_space_and_symlinks_create_no_transaction(self):
        for case in ('hash', 'space', 'symlink'):
            with self.subTest(case=case):
                if case == 'hash':
                    with patch.dict(self.meta, sha256='0' * 64), self.assertRaises(api.SafetyError):
                        self.create()
                elif case == 'space':
                    with self.assertRaises(api.SafetyError):
                        STATE['create'](api, self.master, self.parent, self.meta, self.hardware, free_bytes=0)
                else:
                    (self.parent / 'state.img').symlink_to(self.master)
                    with self.assertRaises(api.SafetyError):
                        self.create()
                    (self.parent / 'state.img').unlink()
                self.assertEqual(list(self.parent.iterdir()), [])

    def test_incomplete_transaction_is_preserved(self):
        marker = self.parent / '.state-copy-in-progress'
        marker.write_text('fixture interrupted')
        with self.assertRaises(api.SafetyError):
            self.create()
        self.assertEqual(marker.read_text(), 'fixture interrupted')

    def test_dirty_state_recovery_keeps_hardware_receipt_and_original(self):
        target = self.create()
        subprocess.run(['debugfs', '-w', '-R', 'ssv state 0', str(target)], check=True, capture_output=True)
        before = api.digest(target)
        info = json.loads((self.parent / 'state-image.json').read_text())
        helper = runpy.run_path(str(ROOT / 'runtime/root-maintenance.py'))['maintain']
        actual = api.run
        def run(*args, **kwargs):
            if args[0] == 'losetup':
                return ''
            self.assertNotIn(args[0], ('mount', 'umount'))
            return actual(*args, **kwargs)
        with patch.object(api, 'run', side_effect=run):
            helper(api, target, info, {'version': 'state-v1', 'sha256': info['original_sha256']},
                   None, self.parent, free_bytes=2 * 1024**3, prefix='state')
        self.assertEqual(json.loads((self.parent / 'state-image.json').read_text()), info)
        self.assertEqual(api.digest(next(self.parent.glob('state-backup-*.img'))), before)
        self.assertFalse((self.parent / 'root-image.json').exists())
        api.validate_ext4(target)

    def test_failed_state_setup_keeps_initial_root_transaction_blocked(self):
        manifest = {'version': '0.9.0-shared', 'size': self.meta['size'], 'sha256': self.meta['sha256']}
        def fail():
            raise RuntimeError('fixture state creation interrupted')
        with self.assertRaises(RuntimeError):
            api.provision(self.master, self.parent, manifest, free_bytes=2 * 1024**3, prepare_new=fail)
        self.assertFalse((self.parent / 'root.img').exists())
        self.assertTrue((self.parent / '.root.img.partial').is_file())
        with self.assertRaises(api.SafetyError):
            api.provision(self.master, self.parent, manifest)

    def initialize_tree(self):
        tree = self.root / 'mounted-fixture'
        for path in ('home/gpu', 'system/gpu-root', 'cache/gpu-inference'):
            (tree / path).mkdir(parents=True)
        for name in ('machine-id', 'hostname'):
            (tree / 'system' / name).write_text('')
        (tree / 'layout.json').write_text('{"schema":1,"kind":"pair-boot-state"}')
        return tree

    def test_identity_is_stable_and_rejects_another_pc_or_interrupted_setup(self):
        tree = self.initialize_tree()
        first = STATE['initialize'](api, tree, self.hardware)
        self.assertEqual(first, STATE['initialize'](api, tree, self.hardware))
        with self.assertRaisesRegex(api.SafetyError, 'mismatch'):
            STATE['initialize'](api, tree, 'other-PC')
        marker = tree / 'system/gpu-root/identity.json'
        marker.unlink()
        with self.assertRaisesRegex(api.SafetyError, 'Incomplete'):
            STATE['initialize'](api, tree, self.hardware)
        self.assertEqual((tree / 'system/machine-id').read_text().strip(), first['machine_id'])

    def test_attachment_orders_state_before_binds_and_unwinds_on_invalid_layout(self):
        sysroot = self.root / 'sysroot'
        sysroot.mkdir()
        tree = self.initialize_tree()
        tree.rename(sysroot / 'persist')
        for source, destination in STATE['BINDINGS']:
            target = sysroot / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            if source in ('system/machine-id', 'system/hostname'):
                target.touch()
            else:
                target.mkdir(exist_ok=True)
        calls = []
        def run(*args):
            calls.append(args)
            return '/dev/loop-fixture' if args[0] == 'losetup' and '--find' in args else ''
        mounts = []
        with patch.object(api, 'run', side_effect=run):
            loop = STATE['attach'](api, self.parent / 'state.img', self.hardware, sysroot, mounts)
        self.assertEqual(loop, '/dev/loop-fixture')
        self.assertEqual(mounts[0], sysroot / 'persist')
        self.assertEqual(len(mounts), 1 + len(STATE['BINDINGS']))
        self.assertTrue((sysroot / 'persist/system/gpu-root/identity.json').is_file())
        (sysroot / 'persist/layout.json').write_text('{"schema":99}')
        mounts = []
        calls.clear()
        with patch.object(api, 'run', side_effect=run), self.assertRaises(api.SafetyError):
            STATE['attach'](api, self.parent / 'state.img', self.hardware, sysroot, mounts)
        self.assertEqual(mounts, [])
        self.assertEqual(calls[-2:], [('umount', str(sysroot / 'persist')), ('losetup', '-d', '/dev/loop-fixture')])

    def test_symlink_inside_state_is_rejected_before_identity_creation(self):
        tree = self.initialize_tree()
        (tree / 'system/machine-id').unlink()
        (tree / 'system/machine-id').symlink_to(self.master)
        with self.assertRaises(api.SafetyError):
            STATE['initialize'](api, tree, self.hardware)
        self.assertFalse((tree / 'system/gpu-root/identity.json').exists())


class BuildStateBoundaryTests(unittest.TestCase):
    def test_inherited_user_home_and_links_are_not_build_inputs(self):
        builder = runpy.run_path(str(ROOT / 'scripts/build-root-appliance.py'))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            builder['require_empty_home'](root)
            home = root / 'home/gpu'
            home.mkdir(parents=True)
            builder['require_empty_home'](root)
            (home / 'user-settings').write_text('synthetic state')
            with self.assertRaisesRegex(ValueError, 'empty'):
                builder['require_empty_home'](root)
            (home / 'user-settings').unlink()
            home.rmdir()
            home.symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'linked'):
                builder['require_empty_home'](root)



class RootReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.target = self.parent / 'root.img'
        self.master = self.parent / 'master.img'
        tree = self.parent / 'seed'
        (tree / 'usr/lib/gpu-root').mkdir(parents=True)
        for path, version in ((self.target, '0.9.0-shared'), (self.master, '0.9.1-shared')):
            (tree / 'usr/lib/gpu-root/image.json').write_text(json.dumps({'version': version, 'state_schema': 1}))
            with path.open('wb') as stream:
                stream.truncate(32 * 1024**2)
            subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-F', '-d', str(tree), str(path)], check=True, capture_output=True)
        self.receipt = {'schema': 1, 'version': '0.9.0-shared', 'state_schema': 1,
                        'size': self.target.stat().st_size, 'original_sha256': api.digest(self.target)}
        (self.parent / 'root-image.json').write_text(json.dumps(self.receipt))
        self.manifest = {'version': '0.9.1-shared', 'persistent_state': {'schema': 1},
                         'size': self.master.stat().st_size, 'sha256': api.digest(self.master)}
        self.state = self.parent / 'state.img'
        self.state.write_bytes(b'synthetic retained user settings')

    def replace(self, ask=lambda *args: True, **kwargs):
        actual = api.run
        def run(*args, **kw):
            if args[0] == 'losetup':
                self.assertEqual(args[1], '-j')
                return ''
            self.assertNotIn(args[0], ('mount', 'umount'))
            return actual(*args, **kw)
        with patch.object(api, 'run', side_effect=run):
            return REPLACE['replace'](api, self.master, self.target, self.receipt, self.manifest,
                                      ask=ask, free_bytes=2 * 1024**3, **kwargs)

    def test_confirmed_update_preserves_old_root_receipt_and_state(self):
        before = self.target.read_bytes()
        state = self.state.read_bytes()
        self.replace()
        self.assertEqual(api.digest(self.target), self.manifest['sha256'])
        backups = list(self.parent.glob('root-before-update-*.img'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)
        self.assertEqual(json.loads(backups[0].with_suffix('.json').read_text()), self.receipt)
        self.assertEqual(self.state.read_bytes(), state)
        self.assertEqual(json.loads((self.parent / 'root-image.json').read_text())['version'], '0.9.1-shared')

    def test_cancellation_legacy_and_downgrade_do_not_mutate_images(self):
        before = api.digest(self.target)
        with self.assertRaisesRegex(api.SafetyError, 'cancelled'):
            self.replace(ask=lambda *args: False)
        with patch.dict(self.receipt, state_schema=0), self.assertRaises(api.SafetyError):
            self.replace()
        with patch.dict(self.manifest, version='0.8.0-shared'), self.assertRaises(api.SafetyError):
            self.replace()
        self.assertEqual(api.digest(self.target), before)
        self.assertFalse(list(self.parent.glob('.root-replace*')))

    def test_interruption_between_renames_blocks_provisioning_and_preserves_backup(self):
        before = api.digest(self.target)
        def crash():
            raise RuntimeError('fixture power interruption')
        with self.assertRaises(RuntimeError):
            self.replace(after_rename=crash)
        self.assertFalse(self.target.exists())
        backups = list(self.parent.glob('root-before-update-*.img'))
        self.assertEqual(api.digest(backups[0]), before)
        with self.assertRaisesRegex(api.SafetyError, 'Interrupted'):
            api.provision(self.master, self.parent, self.manifest)
        self.assertTrue((self.parent / '.root-replace.img').is_file())

    def test_wrong_hash_and_existing_partial_stop_before_confirmation(self):
        called = []
        def ask(*args):
            called.append(args)
            return True
        with patch.dict(self.manifest, sha256='0' * 64), self.assertRaises(api.SafetyError):
            self.replace(ask=ask)
        (self.parent / '.root-replace.img').write_bytes(b'preserve')
        with self.assertRaises(api.SafetyError):
            self.replace(ask=ask)
        self.assertEqual(called, [])
        self.assertEqual((self.parent / '.root-replace.img').read_bytes(), b'preserve')
