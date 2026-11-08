"""Explicit test registration never weakens production GPU or storage policy."""
import importlib.util
import contextlib
import hashlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


fleet = load('no_gpu_fleet', 'runtime/fleet-registry.py')
boot = load('no_gpu_boot', 'runtime/gpu-root.py')
test_boot = load('no_gpu_presentation', 'runtime/no-gpu-test.py')
tool = load('no_gpu_tool', 'toolkit/fleet.py')
refresh = load('no_gpu_refresh', 'scripts/refresh-boot-only.py')


def record():
    return {'schema': 1, 'hardware_uuid': '01234567-89ab-cdef-0123-456789abcdef',
            'ntfs_uuid': '0123456789ABCDEF', 'gpu_bdf': None, 'gpu_pci_id': None,
            'boot_mode': 'no-gpu-test', 'checked_at_unix': int(time.time()) - 5,
            'bitlocker_volume_status': 'FullyDecrypted', 'bitlocker_protection': 'Off',
            'fast_startup_enabled': False, 'secure_boot_enabled': True,
            'partition_is_boot': False, 'partition_is_system': False, 'partition_is_hidden': False,
            'storage_directory': 'GPU-Linux-test', 'storage_target': {
                'kind': 'usb-ntfs-v1', 'partition_guid': '12345678-1234-1234-1234-123456789abc',
                'disk_guid': '23456789-1234-1234-1234-123456789abc',
                'offset_bytes': 12 * 1024**3, 'size_bytes': 16 * 1024**3}}


class NoGpuTests(unittest.TestCase):
    def test_boot_refresh_refuses_existing_outputs_wrong_versions_and_corruption(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as folder:
            base = Path(folder)
            source = base / 'source'
            images = source / 'usb-tree/gpu-linux'
            images.mkdir(parents=True)
            payload = b'fixture image'
            checksum = hashlib.sha256(payload).hexdigest()
            (images / 'root.img').write_bytes(payload)
            (images / 'state.img').write_bytes(payload)
            manifest = {'version': '0.9.0-shared', 'shared_registry': True,
                        'secure_boot_signed_artifacts': True, 'sha256': checksum,
                        'persistent_state': {'sha256': checksum}}
            metadata = images / 'master.json'
            metadata.write_text(json.dumps(manifest))
            output = base / 'output'
            output.mkdir()
            sentinel = output / 'keep'
            sentinel.write_text('original')
            with self.assertRaisesRegex(ValueError, 'new, non-overlapping'):
                refresh.refresh(source, output)
            self.assertEqual(sentinel.read_text(), 'original')
            other = base / 'fresh'
            metadata.write_text(json.dumps({**manifest, 'version': '0.8.0-shared'}))
            with self.assertRaisesRegex(ValueError, '0.9.0'):
                refresh.refresh(source, other)
            self.assertFalse(other.exists())
            metadata.write_text(json.dumps(manifest))
            (images / 'root.img').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                refresh.refresh(source, other)
            self.assertFalse(other.exists())
            alias = base / 'alias'
            alias.symlink_to(source, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'Symlink'):
                refresh.refresh(alias, other)

    def test_registration_preview_commit_and_unknown_evidence_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            mount = base / 'config'
            mount.mkdir()
            (mount / 'usb-config.json').write_text(json.dumps({
                'schema': 1, 'kind': 'gpu-linux-config', 'root_version': '0.9.0-shared'}))
            hardware = base / 'hardware'
            hardware.write_text(record()['hardware_uuid'])
            pci = base / 'pci'
            pci.mkdir()
            value = record()
            del value['boot_mode']
            inventory = base / 'candidate.json'
            inventory.write_text(json.dumps(value))
            def paths(path):
                return {'/sys/class/dmi/id/product_uuid': hardware,
                        '/sys/bus/pci/devices': pci}.get(str(path), Path(path))
            def command(*args):
                return 'ntfs' if '-p' in args else '/dev/fixture'
            with patch.object(tool, 'Path', side_effect=paths), patch.object(tool, 'live_config', return_value='/dev/config-fixture'), \
                 patch.object(tool, 'run', side_effect=command), \
                 patch.object(tool.storage, 'verify_not_reserved_on_boot_usb'), \
                 patch.object(tool.storage, 'inspect_usb_target', return_value=value['storage_target']), \
                 contextlib.redirect_stdout(io.StringIO()):
                context = tool.registration_context(inventory, None, mount, no_gpu_test=True)
                self.assertEqual(context['record']['boot_mode'], 'no-gpu-test')
                digest = tool.registration_digest(context, mount)
                registry = mount / 'gpu-config/registry.json'
                with self.assertRaisesRegex(ValueError, '--confirm REGISTER'):
                    tool.register(inventory, None, mount, '', no_gpu_test=True)
                self.assertFalse(registry.exists())
                with self.assertRaisesRegex(ValueError, 'changed after preview'):
                    tool.register(inventory, None, mount, 'REGISTER', expected_review='0' * 64, no_gpu_test=True)
                self.assertFalse(registry.exists())
                tool.register(inventory, None, mount, 'REGISTER', expected_review=digest, no_gpu_test=True)
                self.assertEqual(tool.fleet.load(registry)['records'][0], context['record'])
                before = registry.read_bytes()
                inventory.write_text(json.dumps({**value, 'bitlocker_protection': 'Unknown'}))
                with self.assertRaisesRegex(ValueError, 'BitLocker'):
                    tool.register(inventory, None, mount, 'REGISTER', no_gpu_test=True)
                self.assertEqual(registry.read_bytes(), before)
                inventory.write_text(json.dumps({**value, 'kind': 'windows-standard-candidate'}))
                with patch.object(tool, 'resolve_candidate', side_effect=ValueError('BitLocker evidence unknown')) as resolve:
                    with self.assertRaisesRegex(ValueError, 'evidence unknown'):
                        tool.registration_context(inventory, None, mount, no_gpu_test=True)
                    resolve.assert_called_once()

    def test_test_record_keeps_all_storage_guards(self):
        self.assertEqual(fleet.record(record())['boot_mode'], 'no-gpu-test')
        for key, value in [('bitlocker_protection', 'Unknown'), ('fast_startup_enabled', True),
                           ('secure_boot_enabled', None), ('partition_is_hidden', None),
                           ('partition_is_boot', True), ('storage_directory', '../bad'),
                           ('gpu_bdf', '0000:01:00.0'), ('gpu_pci_id', '10de:2504'),
                           ('boot_mode', 'arbitrary'), ('checked_at_unix', 0)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                fleet.record({**record(), key: value})
        for key in ('boot_mode', 'storage_target'):
            value = record()
            del value[key]
            with self.assertRaises(ValueError):
                fleet.record(value)

    def test_only_explicit_matching_menu_can_boot_test_registration(self):
        config = {'gpu_root.no_gpu_test': '1', 'gpu_root.shared_registry': '1'}
        self.assertTrue(boot.check_boot_mode(config, record()))
        for changed in ({}, {**config, 'gpu_root.no_gpu_test': '0'},
                        {**config, 'gpu_root.shared_registry': '0'},
                        {**config, 'gpu_unlock.targets': ''}):
            with self.subTest(config=changed), self.assertRaises(boot.SafetyError):
                boot.check_boot_mode(changed, record())
        with self.assertRaises(boot.SafetyError):
            boot.check_boot_mode(config, {'gpu_bdf': '0000:01:00.0'})
        self.assertFalse(boot.check_boot_mode({}, {'gpu_bdf': '0000:01:00.0'}))
        with self.assertRaisesRegex(boot.SafetyError, 'Duplicate'):
            boot.cmdline('gpu_root.no_gpu_test=1 gpu_root.no_gpu_test=0')
        with self.assertRaises(boot.SafetyError):
            boot.check_boot_mode(boot.cmdline('gpu_root.no_gpu_test=1 gpu_root.shared_registry=1 gpu_unlock.targets=x'), record())

    def test_gpu_detection_requires_actual_absence_and_readable_sysfs(self):
        with tempfile.TemporaryDirectory() as folder:
            pci = Path(folder)
            device = pci / '0000:00:02.0'
            device.mkdir()
            vendor = device / 'vendor'
            vendor.write_text('0x8086\n')
            boot.require_no_nvidia(pci)
            vendor.write_text('0x10de\n')
            with self.assertRaisesRegex(boot.SafetyError, 'without NVIDIA'):
                boot.require_no_nvidia(pci)
            vendor.unlink()
            with self.assertRaises(OSError):
                boot.require_no_nvidia(pci)

    def test_deploy_cannot_automatically_start_test_or_change_production_host(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / 'usb-config.json').write_text(json.dumps({
                'schema': 1, 'kind': 'gpu-linux-config', 'root_version': '0.9.0-shared'}))
            value = fleet.merge([record()], 1, '0.9.0-shared')
            fleet.deploy(value, target)
            env = (target / ('gpu-config/hosts/' + record()['hardware_uuid'] + '.env')).read_bytes()
            self.assertEqual(len(env), 1024)
            self.assertIn(b'gpu_default=0\n', env)
            changed = {**record(), 'gpu_bdf': '0000:01:00.0', 'gpu_pci_id': '10de:2504'}
            del changed['boot_mode']
            with self.assertRaisesRegex(ValueError, 'remove/change'):
                fleet.deploy(fleet.merge([changed], 2, '0.9.0-shared'), target)

    def test_early_module_failure_never_becomes_test_fallback(self):
        hook = ROOT / 'runtime/dracut/95gpu-root/early-unlock.sh'
        shell = ('root=gpu-root; getarg() { echo "$test_flag"; }; '
                 'modprobe() { echo MODULE_ATTEMPT; return 1; }; '
                 'warn() { :; }; emergency_shell() { exit 23; }; . "$1"')
        for flag, expected in [('1', 0), ('', 23), ('0', 23)]:
            result = subprocess.run(['sh', '-c', 'test_flag=' + flag + '; ' + shell, 'fixture', str(hook)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assertEqual('MODULE_ATTEMPT' in result.stdout, flag != '1')

    def test_ram_presentation_leaves_root_files_unchanged_and_masks_session(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root = base / 'root'
            banner = root / 'usr/lib/gpu-root/boot-banner.py'
            banner.parent.mkdir(parents=True)
            banner.write_bytes((ROOT / 'runtime/boot-banner.py').read_bytes())
            before = banner.read_bytes()
            state = base / 'run/gpu-root'
            state.mkdir(parents=True)
            units = base / 'run/systemd/system'
            original_copy = test_boot.shutil.copyfile
            def copy(source, destination):
                if str(source) == '/usr/lib/no-gpu-test.py':
                    source = ROOT / 'runtime/no-gpu-test.py'
                return original_copy(source, destination)
            with patch.object(test_boot, 'Path', return_value=units), patch.object(test_boot.shutil, 'copyfile', side_effect=copy):
                test_boot.install(root, state)
            self.assertEqual(banner.read_bytes(), before)
            self.assertEqual((units / 'gpu-pair-session.service').readlink(), Path('/dev/null'))
            self.assertIn('BindReadOnlyPaths=', (units / 'gpu-pair.service.d/90-no-gpu-test.conf').read_text())
            image = base / 'image.json'
            image.write_text('{"version":"0.9.0-shared"}')
            current = state / 'state.json'
            current.write_text('{"mode":"TEST"}')
            original = runpy.run_path(str(state / 'original-banner.py'))
            with patch('runpy.run_path', return_value=original):
                text = test_boot.render(image, current)
            self.assertIn('Version: 0.9.0-shared    Mode: TEST / no GPU', text)


if __name__ == '__main__':
    unittest.main()
