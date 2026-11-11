"""Repair only generated ext4 files; no mount, physical storage or power calls."""
import importlib.util
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('maintenance_api', ROOT / 'runtime/gpu-root.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
helper = runpy.run_path(str(ROOT / 'runtime/root-maintenance.py'))


class RootMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.image = self.parent / 'root.img'
        with self.image.open('wb') as out:
            out.truncate(32 * 1024 * 1024)
        subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-F', str(self.image)], check=True, capture_output=True)
        self.manifest = {'version': '0.8.0-shared', 'kernel_release': 'fixture',
                         'sha256': 'a' * 64, 'size': self.image.stat().st_size}
        self.receipt = {'schema': 1, 'version': self.manifest['version'],
                        'original_sha256': self.manifest['sha256'], 'size': self.image.stat().st_size}
        (self.parent / 'root-image.json').write_text(json.dumps(self.receipt))

    def dirty(self):
        subprocess.run(['debugfs', '-w', '-R', 'ssv state 0', str(self.image)], check=True, capture_output=True)

    def maintain(self, **kwargs):
        real_run = api.run
        def run(*args, **kw):
            if args[0] == 'losetup':
                self.assertEqual(args[1], '-j')
                return ''
            self.assertNotIn(args[0], ('mount', 'umount'))
            return real_run(*args, **kw)
        with patch.object(api, 'run', side_effect=run):
            return helper['maintain'](api, self.image, self.receipt, self.manifest,
                                      self.parent / 'unused-payload', self.parent,
                                      free_bytes=2 * 1024**3, **kwargs)

    def test_clean_root_is_not_copied_or_changed(self):
        before = api.digest(self.image)
        self.maintain()
        self.assertEqual(api.digest(self.image), before)
        self.assertFalse(list(self.parent.glob('root-backup-*')))

    def test_dirty_copy_is_repaired_and_original_retained(self):
        self.dirty()
        before = api.digest(self.image)
        self.maintain()
        api.validate_ext4(self.image)
        backup = list(self.parent.glob('root-backup-*.img'))
        self.assertEqual(len(backup), 1)
        self.assertEqual(api.digest(backup[0]), before)
        self.assertFalse((self.parent / '.root-maintenance.json').exists())
        self.assertTrue(list(self.parent.glob('root-maintenance-*.log')))

    def test_low_space_stops_before_creating_transaction(self):
        self.dirty()
        with patch.object(helper['maintain'].__globals__['shutil'], 'disk_usage', return_value=type('Usage', (), {'free': 0})()):
            with patch.object(api, 'run', side_effect=lambda *a: '' if a[0] == 'losetup' else 'ext4'):
                with self.assertRaisesRegex(api.SafetyError, 'insufficient space'):
                    helper['maintain'](api, self.image, self.receipt, self.manifest, self.parent / 'none', self.parent)
        self.assertFalse((self.parent / '.root-maintenance.json').exists())

    def test_fsck_failure_preserves_original_and_stops_next_provision(self):
        self.dirty()
        before = api.digest(self.image)
        actual = subprocess.run
        def run(args, **kwargs):
            if args[0] == 'e2fsck' and '-p' in args:
                return subprocess.CompletedProcess(args, 4)
            return actual(args, **kwargs)
        with patch.object(subprocess, 'run', side_effect=run):
            with self.assertRaisesRegex(api.SafetyError, 'exit=4'):
                self.maintain()
        self.assertEqual(api.digest(self.image), before)
        self.assertTrue((self.parent / '.root-maintenance.json').is_file())
        with self.assertRaisesRegex(api.SafetyError, 'Interrupted root maintenance'):
            api.provision(self.image, self.parent, self.manifest)

    def test_attached_image_refuses_recovery(self):
        with patch.object(api, 'run', return_value='/dev/loop999: fixture'):
            with self.assertRaisesRegex(api.SafetyError, 'already attached'):
                helper['maintain'](api, self.image, self.receipt, self.manifest, self.parent / 'none', self.parent)

    def test_interruption_between_renames_never_provisions_fresh_root(self):
        self.dirty()
        before = api.digest(self.image)
        actual = api.atomic_new
        def fail_after_backup(source, destination):
            if Path(source).name == '.root-maintenance.img':
                raise OSError('fixture interrupted switch')
            return actual(source, destination)
        with patch.object(api, 'atomic_new', side_effect=fail_after_backup):
            with self.assertRaisesRegex(OSError, 'interrupted switch'):
                self.maintain()
        self.assertFalse(self.image.exists())
        self.assertEqual(api.digest(next(self.parent.glob('root-backup-*.img'))), before)
        with self.assertRaisesRegex(api.SafetyError, 'Interrupted root maintenance'):
            api.provision(self.parent / 'not-used-master', self.parent, self.manifest)

    def test_unknown_legacy_receipt_cannot_migrate(self):
        self.receipt['version'] = 'unknown-old'
        with self.assertRaisesRegex(api.SafetyError, 'no reviewed migration'):
            self.maintain()

    def test_journal_recovery_flag_is_cleared_on_the_copy(self):
        subprocess.run(['debugfs', '-w', '-R', 'feature needs_recovery', str(self.image)], check=True, capture_output=True)
        before = api.digest(self.image)
        self.maintain()
        api.validate_ext4(self.image)
        self.assertEqual(api.digest(next(self.parent.glob('root-backup-*.img'))), before)

    def test_reviewed_migration_preserves_user_state_and_original_image(self):
        tree = self.parent / 'seed'
        release = tree / 'usr/lib/gpu-root/image.json'
        release.parent.mkdir(parents=True)
        release.write_text(json.dumps({'version': '0.7.8-shared', 'kernel_release': 'fixture'}))
        user = tree / 'home/gpu/settings.json'
        user.parent.mkdir(parents=True)
        user.write_text('preserve fixture PAIR identity and settings')
        subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-F', '-d', str(tree), str(self.image)], check=True, capture_output=True)
        self.receipt['version'] = '0.7.8-shared'
        self.manifest['sha256'] = 'b' * 64
        self.manifest['migration'] = {'from_version': '0.7.8-shared', 'from_sha256': 'a' * 64,
                                      'from_size': self.image.stat().st_size}
        before = api.digest(self.image)
        updated = self.parent / 'new-release.json'
        updated.write_text(json.dumps({'version': '0.8.0-shared', 'kernel_release': 'fixture'}))
        def upgrade(candidate):
            # Offline writes to a fixture file, never a filesystem mount.
            for command in ('rm /usr/lib/gpu-root/image.json', f'write {updated} /usr/lib/gpu-root/image.json'):
                subprocess.run(['debugfs', '-w', '-R', command, str(candidate)], check=True, capture_output=True)
        self.maintain(upgrade=upgrade)
        self.assertEqual(api.digest(next(self.parent.glob('root-backup-*.img'))), before)
        self.assertEqual(json.loads((self.parent / 'root-image.json').read_text())['version'], '0.8.0-shared')
        self.assertEqual(api.run('debugfs', '-R', 'cat /home/gpu/settings.json', str(self.image)), user.read_text())

    def test_unknown_upgrade_path_and_bad_payload_hash_stop_before_mount(self):
        payload = self.parent / 'payload.tar'
        payload.write_bytes(b'fixture')
        with self.assertRaisesRegex(api.SafetyError, 'payload hash mismatch'):
            helper['apply_upgrade'](api, self.image, payload, {'sha256': '0' * 64}, self.parent)


class Rtx30PolicyTests(unittest.TestCase):
    def test_policy_sources_and_legacy_registry_conversion(self):
        fleet = runpy.run_path(str(ROOT / 'runtime/fleet-registry.py'))
        policy = json.loads((ROOT / 'config/rtx30-gpu-policy.json').read_text())
        self.assertEqual(fleet['GPU_NORMAL_IDS'], set(policy['products']))
        self.assertEqual(fleet['GPU_UNLOCK_IDS'], set(policy['unlock_ids']))
        self.assertIn('10de:2484', fleet['GPU_UNLOCK_IDS'])
        self.assertIn('10de:2582', fleet['GPU_NORMAL_IDS'])
        self.assertNotIn('10de:2582', fleet['GPU_UNLOCK_IDS'])
        host = runpy.run_path(str(ROOT / 'tests/test-fleet-registry.py'))['host']()
        old = fleet['merge']([host], 4, '0.7.8-shared')
        new = fleet['for_version'](old, '0.8.0-shared')
        self.assertEqual(new, old)
        self.assertEqual(fleet['for_version'](old, 'unknown'), old)
        host['gpu_pci_id'] = '10de:2484'
        self.assertEqual(fleet['record'](host)['gpu_pci_id'], '10de:2484')
        self.assertIn(b'gpu_targets=unsupported', fleet['environment'](host['gpu_bdf'], False))
        self.assertIn(b'gpu_default=0', fleet['environment'](host['gpu_bdf'], True, True))
        powershell = (ROOT / 'toolkit/fleet.ps1').read_text()
        for pci in policy['products']:
            self.assertIn(pci, powershell)

    def test_gui_offers_rtx30_but_not_other_generations(self):
        gui = runpy.run_path(str(ROOT / 'gui/fedora-gui.py'))
        with tempfile.TemporaryDirectory() as folder:
            sysfs = Path(folder)
            for index, pci in enumerate(('2504', '2484', '2582', '2684')):
                device = sysfs / f'0000:0{index + 1}:00.0'
                device.mkdir()
                (device / 'vendor').write_text('0x10de')
                (device / 'device').write_text('0x' + pci)
            self.assertEqual(gui['gpu_choices'](sysfs), ['0000:01:00.0', '0000:02:00.0', '0000:03:00.0'])
