"""Console choices use fake PAIR/power calls; never invoke host power actions."""
import io
import json
import os
from pathlib import Path
import pty
import runpy
import tempfile
import termios
import unittest

ROOT = Path(__file__).resolve().parents[1]
console = runpy.run_path(str(ROOT / 'runtime/pair-console.py'))


class PairConsoleTests(unittest.TestCase):
    def session(self, choices, launch_status=0, power_status=0):
        events = []
        output = io.StringIO()

        def launch():
            events.append('pair')
            return launch_status

        def power(action):
            events.append(action)
            return power_status

        result = console['menu']('fixture USB notice', io.StringIO(choices), output,
                                 launch, power, lambda: events.append('wait'))
        self.assertEqual(result, 0)
        return events, output.getvalue()

    def test_empty_console_does_not_autostart_or_poweroff(self):
        events, text = self.session('')
        self.assertEqual(events, [])
        self.assertIn('fixture USB notice', text)
        self.assertIn('Start PAIR', text)

    def test_pair_quit_returns_to_menu_then_poweroff(self):
        events, text = self.session('1\n3\n1\n')
        self.assertEqual(events, ['pair', 'poweroff', 'wait'])
        self.assertIn('Restart PAIR', text)

    def test_explicit_enter_starts_pair_and_can_restart_it(self):
        events, _ = self.session('\n1\n2\n')
        self.assertEqual(events, ['pair', 'pair', 'reboot', 'wait'])

    def test_invalid_input_and_power_rejection_do_not_exit_or_launch(self):
        events, text = self.session('reboot\n3\n', power_status=1)
        self.assertEqual(events, ['poweroff'])
        self.assertIn('No action was taken', text)
        self.assertIn('request rejected', text)

    def test_pair_failure_returns_to_choices(self):
        events, text = self.session('1\n2\n', launch_status=17)
        self.assertEqual(events, ['pair', 'reboot', 'wait'])
        self.assertIn('status=17', text)

    def test_missing_pair_or_systemctl_is_reported(self):
        def missing(*args):
            raise FileNotFoundError('fixture missing executable')

        output = io.StringIO()
        console['menu']('notice', io.StringIO('1\n2\n'), output, missing, missing, missing)
        self.assertIn('PAIR could not start', output.getvalue())
        self.assertIn('Power request failed', output.getvalue())

    def test_usb_notice_requires_shutdown_ready_and_usb_verification(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state, ready, shutdown = (root / name for name in ('state', 'ready', 'shutdown'))
            state.write_text(json.dumps({'root_storage_transport': 'nvme'}))
            ready.write_text('{"ready":true}')
            shutdown.write_text('fixture')
            shutdown.chmod(0o700)
            checks = []
            verify = lambda value: checks.append(value)
            notice = console['usb_notice'](state, ready, shutdown, verify)
            self.assertIn('USB CAN BE REMOVED NOW', notice)
            self.assertEqual(len(checks), 1)

            def mounted(value):
                raise ValueError('a USB partition is still mounted')

            notice = console['usb_notice'](state, ready, shutdown, mounted)
            self.assertIn('KEEP USB INSERTED', notice)
            ready.write_text('{"ready":false}')
            notice = console['usb_notice'](state, ready, shutdown, verify)
            self.assertIn('KEEP USB INSERTED', notice)
            self.assertEqual(len(checks), 1)

    def test_separate_root_usb_must_stay_inserted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state, ready, shutdown = (root / name for name in ('state', 'ready', 'shutdown'))
            state.write_text('{"root_storage_transport":"usb"}')
            ready.write_text('{"ready":true}')
            shutdown.touch(mode=0o700)
            notice = console['usb_notice'](state, ready, shutdown, lambda _: None)
            self.assertIn('USB CAN BE REMOVED NOW', notice)
            self.assertIn('separate root/Models storage USB inserted', notice)

    def test_terminal_input_is_restored_after_tui_raw_mode(self):
        master, slave = pty.openpty()
        try:
            with os.fdopen(os.dup(slave)) as source:
                restore = console['terminal_restore'](source)
                original = termios.tcgetattr(slave)
                raw = termios.tcgetattr(slave)
                raw[3] &= ~(termios.ECHO | termios.ICANON)
                termios.tcsetattr(slave, termios.TCSANOW, raw)
                restore()
                self.assertEqual(termios.tcgetattr(slave), original)
        finally:
            os.close(master)
            os.close(slave)
