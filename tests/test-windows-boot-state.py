"""Windows boot-state guards use mock devices and generated NTFS files only."""
from contextlib import ExitStack, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('windows_boot_api', ROOT / 'runtime/gpu-root.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
STATE = runpy.run_path(str(ROOT / 'runtime/persistent-state.py'))
SYSROOT = ROOT / 'build/appliance-0.9.1-r4/root-tree'


class WindowsBootStateTests(unittest.TestCase):
    def inspect(self, other='not-detected', selected='not-detected', encrypted=False):
        inventory = {'blockdevices': [
            {'path': '/dev/fixture-root', 'fstype': 'ntfs'},
            {'path': '/dev/fixture-windows', 'fstype': 'BitLocker' if encrypted else 'ntfs'}]}
        def command(args, **kwargs):
            status = selected if args[-1] == '/dev/fixture-root' else other
            return subprocess.CompletedProcess(args, 0, json.dumps({'schema': 1, 'status': status}), '')
        with patch.object(api, 'run', return_value=json.dumps(inventory)), \
             patch.object(api.subprocess, 'run', side_effect=command) as probe:
            api.check_windows_hibernation('/dev/fixture-root')
            return probe.call_args_list

    def test_hibernated_windows_volume_stops_even_when_root_is_on_another_volume(self):
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(api.SafetyError, 'hibernation/Fast Startup'):
            self.inspect(other='hibernated')

    def test_encrypted_or_unreadable_other_volume_is_reported_without_global_safety_claim(self):
        for encrypted, state in ((True, 'not-detected'), (False, 'unknown')):
            output = io.StringIO()
            with redirect_stdout(output):
                calls = self.inspect(other=state, encrypted=encrypted)
            self.assertIn('Windows-wide hibernation state is unknown', output.getvalue())
            self.assertEqual(len(calls), 1 if encrypted else 2)

    def test_unreadable_root_or_malformed_probe_result_stops(self):
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(api.SafetyError, 'Cannot inspect root storage'):
            self.inspect(selected='unknown')
        with patch.object(api, 'run', return_value='{"blockdevices":[]}'), \
             patch.object(api.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'bad JSON')), \
             redirect_stdout(io.StringIO()), self.assertRaises(api.SafetyError):
            api.check_windows_hibernation('/dev/fixture-root')

    def test_new_encryption_observed_in_inventory_stops_before_inspection(self):
        inventory = {'blockdevices': [{'path': '/dev/fixture-root', 'fstype': 'BitLocker'}]}
        with patch.object(api, 'run', return_value=json.dumps(inventory)), \
             patch.object(api.subprocess, 'run') as inspect, redirect_stdout(io.StringIO()), \
             self.assertRaisesRegex(api.SafetyError, 'BitLocker volume'):
            api.check_windows_hibernation('/dev/fixture-root')
        inspect.assert_not_called()

    def test_timeout_or_invalid_inventory_stops(self):
        with patch.object(api, 'run', return_value='{"blockdevices":[]}'), \
             patch.object(api.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 30)), \
             redirect_stdout(io.StringIO()), self.assertRaises(api.SafetyError):
            api.check_windows_hibernation('/dev/fixture-root')
        with patch.object(api, 'run', return_value=json.dumps({'blockdevices': [
                {'path': '/dev/../fixture', 'fstype': 'ntfs'}]})), \
             redirect_stdout(io.StringIO()), self.assertRaises(api.SafetyError):
            api.check_windows_hibernation('/dev/fixture-root')

    def test_boot_entry_stops_before_any_root_write_for_encryption_or_hibernation(self):
        for kind in ('BitLocker', 'ntfs'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
                root = Path(folder)
                runtime = root / 'run'
                images = runtime / 'usb/gpu-linux'
                images.mkdir(parents=True)
                for name in ('root.img', 'state.img'):
                    with (images / name).open('wb') as stream:
                        stream.truncate(64 * 1024**2)
                sha = api.digest(images / 'root.img')
                manifest = {'size': 64 * 1024**2, 'sha256': sha, 'kernel_release': os.uname().release,
                            'persistent_state': {'schema': 1, 'filename': 'state.img',
                                                 'size': 64 * 1024**2, 'sha256': sha}}
                initrd = root / 'initrd'
                initrd.touch()
                hardware = root / 'hardware'
                hardware.write_text('fixture PC')
                cmdline = root / 'cmdline'
                cmdline.write_text('')
                mapping = {'/run/gpu-root': runtime, '/etc/initrd-release': initrd,
                           '/sys/class/dmi/id/product_uuid': hardware, '/proc/cmdline': cmdline}
                stack.enter_context(patch.object(api, 'Path', side_effect=lambda p: mapping.get(str(p), Path(p))))
                stack.enter_context(patch.object(api, 'require_no_nvidia'))
                stack.enter_context(patch.object(api.os, 'getpid', return_value=42))
                stack.enter_context(patch.object(api, 'device', side_effect=['/dev/fixture-usb', '/dev/fixture-root']))
                stack.enter_context(patch.object(api, 'manifest_at', return_value=manifest))
                stack.enter_context(patch.object(api, 'shared_host_profile', return_value={
                    'ntfs_uuid': '0123456789ABCDEF', 'boot_mode': 'no-gpu-test', 'storage_target': {}}))
                stack.enter_context(patch.object(api, 'validate_ext4'))
                stack.enter_context(patch.object(api, 'verify_root_storage', return_value={}))
                actual_run_path = runpy.run_path
                stack.enter_context(patch.object(runpy, 'run_path', side_effect=lambda p: STATE if p == '/usr/lib/persistent-state.py' else actual_run_path(p)))
                writes = stack.enter_context(patch.object(api, 'mount_readwrite'))
                copies = stack.enter_context(patch.object(api, 'provision'))
                stack.enter_context(patch.object(api.os.path, 'ismount', return_value=False))
                calls = []
                def command(*args):
                    calls.append(args)
                    if args[0] in ('mount', 'umount'):
                        return ''
                    if args[0] == 'blkid':
                        return 'ext4' if args[-1] == '/dev/fixture-usb' else kind
                    if args[0] == 'lsblk':
                        return json.dumps({'blockdevices': []})
                    raise AssertionError(args)
                stack.enter_context(patch.object(api, 'run', side_effect=command))
                def subprocess_run(args, **kwargs):
                    if args[0] == 'findmnt':
                        return subprocess.CompletedProcess(args, 1)
                    self.assertEqual(args[:2], ['/usr/bin/python3', '/usr/lib/ntfs-hibernation.py'])
                    return subprocess.CompletedProcess(args, 0, '{"schema":1,"status":"hibernated"}')
                stack.enter_context(patch.object(api.subprocess, 'run', side_effect=subprocess_run))
                stack.enter_context(redirect_stdout(io.StringIO()))
                reason = 'BitLocker volume' if kind == 'BitLocker' else 'hibernation/Fast Startup'
                with self.assertRaisesRegex(api.SafetyError, reason):
                    api.boot({'gpu_root.allow_ntfs_write': '1', 'gpu_root.shared_registry': '1',
                              'gpu_root.no_gpu_test': '1'})
                writes.assert_not_called()
                copies.assert_not_called()
                self.assertFalse(any('/sysroot' in args for args in calls))


@unittest.skipUnless((SYSROOT / 'usr/sbin/mkntfs').is_file() and shutil.which('bwrap'),
                     'Optional generated Debian sysroot needed for NTFS library fixture')
class NtfsHibernationLibraryTests(unittest.TestCase):
    def test_readonly_library_handles_missing_active_inactive_and_short_headers_without_changes(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as folder:
            work = Path(folder)
            template = work / 'template.img'
            with template.open('wb') as stream:
                stream.truncate(64 * 1024**2)
            shutil.copyfile(ROOT / 'runtime/ntfs-hibernation.py', work / 'probe.py')
            base = ['bwrap', '--unshare-all', '--ro-bind', str(SYSROOT), '/', '--bind', str(work), '/work',
                    '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp', '--clearenv', '--setenv', 'PATH',
                    '/usr/sbin:/usr/bin:/sbin:/bin', '--']
            def run(args):
                return subprocess.run(base + args, check=True, capture_output=True, text=True)
            run(['mkntfs', '-F', '-Q', '/work/template.img'])
            for header, expected in ((None, 'not-detected'), (b'hibr' + b'\0' * 4092, 'hibernated'),
                                     (b'\0' * 4096, 'not-detected'), (b'x', 'hibernated')):
                with self.subTest(header=header[:4] if header else None):
                    image = work / 'candidate.img'
                    shutil.copyfile(template, image)
                    if header is not None:
                        (work / 'header').write_bytes(header)
                        run(['ntfscp', '-f', '/work/candidate.img', '/work/header', '/hiberfil.sys'])
                    before = api.digest(image)
                    result = json.loads(run(['python3', '/work/probe.py', '/work/candidate.img']).stdout)
                    self.assertEqual(result['status'], expected)
                    self.assertEqual(api.digest(image), before)
            alias = work / 'alias.img'
            alias.symlink_to('candidate.img')
            before = api.digest(image)
            result = json.loads(run(['python3', '/work/probe.py', '/work/alias.img']).stdout)
            self.assertEqual(result['status'], 'unknown')
            self.assertEqual(api.digest(image), before)


if __name__ == '__main__':
    unittest.main()
