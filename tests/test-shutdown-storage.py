"""Shutdown policy fixtures never open a real loop or invoke real storage tools."""
import errno
import json
import os
from pathlib import Path
import runpy
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
LOOP = runpy.run_path(str(ROOT / 'runtime/detach-loop.py'))


class LoopDetachTests(unittest.TestCase):
    def test_already_detached_is_done_and_no_command_runs(self):
        with patch.dict(LOOP['detach'].__globals__, attached=Mock(return_value=False)), patch('subprocess.run') as run:
            LOOP['detach']('/dev/loop1')
            run.assert_not_called()

    def test_detach_is_confirmed_including_a_concurrent_detach(self):
        for code in (0, 1):
            with self.subTest(code=code):
                query = Mock(side_effect=[True, False])
                with patch.dict(LOOP['detach'].__globals__, attached=query), patch('subprocess.run', return_value=SimpleNamespace(returncode=code)) as run:
                    LOOP['detach']('/dev/loop1')
                    self.assertEqual(run.call_args.args[0], ['losetup', '--detach', '/dev/loop1'])
                    self.assertEqual(query.call_count, 2)

    def test_async_detach_waits_but_busy_and_command_errors_stop(self):
        with patch.dict(LOOP['detach'].__globals__, attached=Mock(side_effect=[True, True, False])), patch('subprocess.run', return_value=SimpleNamespace(returncode=0)), patch('time.sleep') as sleep:
            LOOP['detach']('/dev/loop1')
            sleep.assert_called_once()
        for code in (0, 1):
            with patch.dict(LOOP['detach'].__globals__, attached=Mock(return_value=True)), patch('subprocess.run', return_value=SimpleNamespace(returncode=code)):
                with self.assertRaises(RuntimeError):
                    LOOP['detach']('/dev/loop1', timeout=0)

    def test_query_errors_are_never_treated_as_unconfigured(self):
        for sequence in ([OSError(errno.EIO, 'fixture')], [True, OSError(errno.EPERM, 'fixture')]):
            with patch.dict(LOOP['detach'].__globals__, attached=Mock(side_effect=sequence)), patch('subprocess.run', return_value=SimpleNamespace(returncode=0)):
                with self.assertRaises(OSError):
                    LOOP['detach']('/dev/loop1')

    def test_kernel_status_distinguishes_enxio_and_always_closes_fd(self):
        node = SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(7, 1))
        for error, expected in ((None, True), (errno.ENXIO, False), (errno.EIO, None)):
            with patch('pathlib.Path.lstat', return_value=node), patch('os.open', return_value=42), patch('os.fstat', return_value=node), patch('os.close') as close, patch('fcntl.ioctl', side_effect=OSError(error, 'fixture') if error else None):
                if expected is None:
                    with self.assertRaises(OSError):
                        LOOP['attached']('/dev/loop1')
                else:
                    self.assertEqual(LOOP['attached']('/dev/loop1'), expected)
                close.assert_called_once_with(42)
        with patch('pathlib.Path.lstat', return_value=node), patch('os.open', side_effect=OSError(errno.ENXIO, 'fixture')):
            self.assertFalse(LOOP['attached']('/dev/loop1'))

    def test_wrong_paths_nodes_and_missing_devices_stop_before_io(self):
        with patch('os.open') as open_device:
            for path in ('/dev/sda', '/dev/loop1bad', '/dev/loop1/../sda'):
                with self.assertRaises(ValueError):
                    LOOP['attached'](path)
            for mode, device in ((stat.S_IFREG, os.makedev(7, 1)), (stat.S_IFLNK, os.makedev(7, 1)),
                                 (stat.S_IFBLK, os.makedev(8, 1)), (stat.S_IFBLK, os.makedev(7, 2))):
                with patch('pathlib.Path.lstat', return_value=SimpleNamespace(st_mode=mode, st_rdev=device)):
                    with self.assertRaises(ValueError):
                        LOOP['attached']('/dev/loop1')
            with patch('pathlib.Path.lstat', side_effect=FileNotFoundError('fixture')):
                with self.assertRaises(FileNotFoundError):
                    LOOP['attached']('/dev/loop1')
            open_device.assert_not_called()


class ShutdownHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.state = self.base / 'state'; self.state.mkdir()
        self.marker = self.state / 'state.json'
        self.marker.write_text(json.dumps({'state_loop': '/dev/loop1', 'loop': '/dev/loop0'}))
        self.mounts = ['/oldroot/models', '/oldroot/var/cache/gpu-inference', '/oldroot/etc/hostname',
                       '/oldroot/etc/machine-id', '/oldroot/var/lib/gpu-root', '/oldroot/home/gpu',
                       '/oldroot/persist', '/oldroot', str(self.state / 'ntfs')]
        (self.base / 'mounts').write_text('\n'.join(self.mounts) + '\n')
        self.log = self.base / 'events'; self.log.write_text('')
        hook = (ROOT / 'runtime/dracut/95gpu-root/shutdown-root.sh').read_text()
        hook = hook.replace('/oldsys/run/gpu-root', str(self.state)).replace('/run/gpu-root', str(self.base / 'fallback'))
        (self.base / 'hook.sh').write_text(hook)
        self.shell = r'''
warn() { printf 'WARN:%s\n' "$*"; }
ismounted() {
  while IFS= read -r fixture_mount; do
    [ "$fixture_mount" != "$1" ] || return 0
  done < "$fixture_base/mounts"
  return 1
}
umount() {
  printf 'UNMOUNT:%s\n' "$1" >> "$fixture_base/events"
  if [ -f "$fixture_base/fail-mount" ] && [ "$(cat "$fixture_base/fail-mount")" = "$1" ]; then return 1; fi
  "$fixture_python" -c 'from pathlib import Path; import sys; p=Path(sys.argv[1]); p.write_text("\n".join(x for x in p.read_text().splitlines() if x != sys.argv[2])+"\n")' "$fixture_base/mounts" "$1"
}
python3() {
  if [ "$1" = /usr/lib/detach-loop.py ]; then
    printf 'LOOP:%s\n' "$2" >> "$fixture_base/events"
    [ ! -f "$fixture_base/fail-loop" ] || [ "$(cat "$fixture_base/fail-loop")" != "$2" ] || return 1
    return 0
  fi
  "$fixture_python" "$@"
}
losetup() { echo UNEXPECTED_RAW_LOSETUP >&2; return 99; }
sync() {
  printf 'SYNC:%s\n' "$*" >> "$fixture_base/events"
  [ "$#" = 0 ] || [ ! -f "$fixture_base/fail-sync" ]
}
sleep() { echo FINAL_STOP; exit 77; }
. "$fixture_base/hook.sh"
'''

    def run_hook(self, expected=0, final=False):
        result = subprocess.run(['sh', '-c', self.shell], capture_output=True, text=True,
                                env={**os.environ, 'fixture_base': str(self.base),
                                     'fixture_python': sys.executable, 'final': 'final' if final else ''})
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn('UNEXPECTED_RAW_LOSETUP', result.stderr)
        return result

    def test_cleanup_order_and_repeated_hook_with_no_state(self):
        self.run_hook()
        events = self.log.read_text().splitlines()
        self.assertLess(events.index('UNMOUNT:/oldroot/persist'), events.index('LOOP:/dev/loop1'))
        self.assertLess(events.index('UNMOUNT:/oldroot'), events.index('LOOP:/dev/loop0'))
        self.assertLess(events.index('LOOP:/dev/loop0'), events.index('UNMOUNT:' + str(self.state / 'ntfs')))
        self.assertFalse(self.marker.exists())
        self.run_hook()
        self.assertEqual(self.log.read_text().splitlines(), events)

    def test_dracut_already_unmounted_and_detached_can_finish(self):
        (self.base / 'mounts').write_text(str(self.state / 'ntfs') + '\n')
        self.run_hook()
        self.assertEqual(self.log.read_text().count('LOOP:'), 2)
        self.assertIn('UNMOUNT:' + str(self.state / 'ntfs'), self.log.read_text())
        self.assertFalse(self.marker.exists())

    def test_state_or_root_failure_preserves_state_and_retry_completes(self):
        for device in ('/dev/loop1', '/dev/loop0'):
            with self.subTest(device=device):
                self.marker.write_text(json.dumps({'state_loop': '/dev/loop1', 'loop': '/dev/loop0'}))
                (self.base / 'mounts').write_text('\n'.join(self.mounts) + '\n')
                self.log.write_text('')
                failure = self.base / 'fail-loop'; failure.write_text(device)
                self.run_hook(expected=1)
                self.assertTrue(self.marker.exists())
                self.assertNotIn('UNMOUNT:' + str(self.state / 'ntfs'), self.log.read_text())
                failure.unlink()
                self.run_hook()
                self.assertFalse(self.marker.exists())

    def test_busy_mount_and_failed_ntfs_sync_never_erase_state(self):
        for mountpoint in ('/oldroot/persist', '/oldroot'):
            failure = self.base / 'fail-mount'; failure.write_text(mountpoint)
            self.run_hook(expected=1)
            self.assertTrue(self.marker.exists())
            failure.unlink()
        (self.base / 'fail-sync').touch()
        self.run_hook(expected=1)
        self.assertTrue(self.marker.exists())
        self.assertNotIn('UNMOUNT:' + str(self.state / 'ntfs'), self.log.read_text())

    def test_final_failure_keeps_automatic_poweroff_blocked(self):
        (self.base / 'fail-loop').write_text('/dev/loop1')
        result = self.run_hook(expected=77, final=True)
        self.assertIn('storage cleanup incomplete', result.stdout)
        self.assertIn('FINAL_STOP', result.stdout)
        self.assertTrue(self.marker.exists())
