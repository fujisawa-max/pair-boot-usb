"""Stop only a fixture shell; never invoke systemctl or host shutdown."""
import configparser
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ConsoleShutdownTests(unittest.TestCase):
    def test_configured_stop_signal_ends_interactive_console(self):
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(ROOT / 'runtime/systemd/gpu-root-console.service')
        name = unit['Service'].get('KillSignal', 'SIGTERM')
        stop_signal = getattr(signal, name)
        master, slave = pty.openpty()
        process = subprocess.Popen(['/bin/bash', '--noprofile', '--norc', '-i'],
                                   stdin=slave, stdout=slave, stderr=slave,
                                   start_new_session=True,
                                   env={'PATH': '/usr/bin:/bin', 'TERM': 'dumb', 'PS1': 'fixture-ready> '})
        os.close(slave)
        try:
            data = b''
            deadline = time.monotonic() + 5
            while b'fixture-ready> ' not in data:
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0, 'Fixture shell did not become ready')
                if select.select([master], [], [], remaining)[0]:
                    data += os.read(master, 4096)
            process.send_signal(signal.SIGTERM)
            with self.assertRaises(subprocess.TimeoutExpired):
                process.wait(timeout=.2)
            process.send_signal(stop_signal)
            self.assertEqual(process.wait(timeout=3), -signal.SIGHUP)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)
