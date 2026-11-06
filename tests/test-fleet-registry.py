"""Shared fleet safety checks use temporary files, never USB/Windows devices."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fleet = load('fleet_fixture', 'runtime/fleet-registry.py')
menu = load('fleet_menu', 'scripts/generate-boot-menu.py')
root = load('fleet_boot', 'runtime/gpu-root.py')


def host():
    return {'schema': 1, 'hardware_uuid': '01234567-89ab-cdef-0123-456789abcdef',
            'ntfs_uuid': '0123456789ABCDEF', 'gpu_bdf': '0000:01:00.0',
            'gpu_pci_id': '10de:2504', 'checked_at_unix': int(time.time()) - 5,
            'bitlocker_volume_status': 'FullyDecrypted', 'bitlocker_protection': 'Off',
            'fast_startup_enabled': False, 'secure_boot_enabled': True,
            'partition_is_boot': False, 'partition_is_system': False, 'partition_is_hidden': False,
            'storage_directory': 'example-GPU-Linux'}


class FleetTests(unittest.TestCase):
    def test_merge_duplicates_refresh_and_conflict(self):
        a = host()
        b = {**a, 'checked_at_unix': a['checked_at_unix'] + 1}
        value = fleet.merge([a, b], 1, 'fixture')
        self.assertEqual(len(value['records']), 1)
        self.assertEqual(value['records'][0]['checked_at_unix'], b['checked_at_unix'])
        for field, changed in (('ntfs_uuid', 'FEDCBA9876543210'), ('gpu_bdf', '0000:02:00.0'), ('storage_directory', 'GPU-Linux')):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'Conflicting'):
                fleet.merge([a, {**b, field: changed}], 1, 'fixture')

    def test_registry_checksum_version_generation_and_duplicates(self):
        value = fleet.merge([host()], 1, 'fixture')
        self.assertEqual(fleet.decode(fleet.encode(value)), value)
        envelope = json.loads(fleet.encode(value))
        envelope['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'checksum'):
            fleet.decode(json.dumps(envelope).encode())
        with self.assertRaisesRegex(ValueError, 'generation'):
            fleet.merge([value], 1, 'fixture')
        with self.assertRaisesRegex(ValueError, 'versions'):
            fleet.merge([value], 2, 'different')
        value['records'] *= 2
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            fleet.decode(fleet.encode(value))

    def test_unknown_and_expired_safety_states_rejected(self):
        a = host()
        changes = {'checked_at_unix': 0, 'bitlocker_protection': 'Unknown', 'fast_startup_enabled': True,
                   'partition_is_system': True, 'partition_is_boot': None, 'secure_boot_enabled': False,
                   'gpu_pci_id': '10de:9999', 'storage_directory': '../Windows'}
        for key, value in changes.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                fleet.record({**a, key: value})

    def test_deploy_and_interrupted_environment_cannot_change_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / 'usb-config.json').write_text(json.dumps({'schema': 1, 'kind': 'gpu-linux-config', 'root_version': 'fixture'}))
            first = fleet.merge([host()], 1, 'fixture')
            fleet.deploy(first, target)
            registry = target / 'gpu-config/registry.json'
            original = registry.read_bytes()
            env = target / ('gpu-config/hosts/' + host()['hardware_uuid'] + '.env')
            self.assertEqual(env.stat().st_size, 1024)
            self.assertIn(b'gpu_targets=0000:01:00.0\n', env.read_bytes())
            changed = fleet.merge([{**host(), 'ntfs_uuid': 'FEDCBA9876543210'}], 2, 'fixture')
            with self.assertRaisesRegex(ValueError, 'remove/change'):
                fleet.deploy(changed, target)
            self.assertEqual(registry.read_bytes(), original)
            fleet.deploy(first, target)  # idempotent
            second = fleet.merge([first], 2, 'fixture')
            fleet.deploy(second, target)
            with self.assertRaisesRegex(ValueError, 'rollback'):
                fleet.deploy(first, target)

    def test_shared_grub_only_loads_whitelisted_environment(self):
        text = menu.generate('1234-5678', {}, shared_registry=True)
        self.assertIn('load_env --file="($gpu_disk,gpt3)/gpu-config/hosts/$gpu_host_uuid.env" gpu_targets gpu_default', text)
        self.assertNotIn('source ', text)
        self.assertEqual(text.count('gpu_root.shared_registry=1'), 2)
        kernels = [line for line in text.splitlines() if line.strip().startswith('linux ')]
        self.assertEqual(kernels[1], kernels[0] + ' gpu_unlock.targets=$gpu_targets')

    def test_configuration_partition_must_be_on_same_usb_parent(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            sys_class = base / 'class'
            sys_class.mkdir()
            disk = base / 'devices/sda'
            for number in ('2', '3'):
                part = disk / ('sda' + number)
                part.mkdir(parents=True)
                (part / 'partition').write_text(number)
            (sys_class / 'sda2').symlink_to(disk / 'sda2')
            def path(value):
                return sys_class if value == '/sys/class/block' else Path(value)
            def command(*args):
                if args[0] == 'lsblk':
                    return 'usb'
                return 'vfat' if args[3] == 'TYPE' else 'GPU-LOGS'
            with patch.object(root, 'Path', side_effect=path), patch.object(root, 'run', side_effect=command):
                self.assertEqual(root.configuration_device('/dev/sda2'), '/dev/sda3')
            with patch.object(root, 'Path', side_effect=path), patch.object(root, 'run', return_value='nvme'):
                with self.assertRaisesRegex(root.SafetyError, 'physical USB'):
                    root.configuration_device('/dev/sda2')

    def test_shared_menu_passes_actual_grub_syntax_checker(self):
        import subprocess
        sysroot = ROOT / 'build/appliance-root-final'
        if not (sysroot / 'usr/bin/grub-script-check').exists():
            self.skipTest('Optional workspace GRUB needed')
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as folder:
            path = Path(folder) / 'menu.cfg'
            path.write_text(menu.generate('1234-5678', {}, shared_registry=True))
            result = subprocess.run(['bwrap', '--unshare-all', '--ro-bind', str(sysroot), '/',
                                     '--ro-bind', folder, '/work', '--dev', '/dev', '--proc', '/proc',
                                     '--tmpfs', '/tmp', '--clearenv', '--setenv', 'PATH',
                                     '/usr/sbin:/usr/bin:/sbin:/bin', '--',
                                     'grub-script-check', '/work/menu.cfg'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('syntax error', result.stderr)
