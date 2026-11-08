"""Transient session supervision uses fake systemctl calls, never host services."""
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest

API = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'runtime/pair-session.py'))
DESCRIPTION = API['DESCRIPTION']


def response(text='', code=0):
    return SimpleNamespace(stdout=text, returncode=code)


class PairSessionTests(unittest.TestCase):
    def test_session_drops_uid_uses_cgroup_and_cleans_after_failure(self):
        calls = []
        states = iter(['LoadState=not-found', 'LoadState=loaded\nActiveState=failed\nDescription=' + DESCRIPTION,
                       'LoadState=not-found'])
        def call(command, **kwargs):
            calls.append(command)
            if command[1] == 'show':
                return response(next(states))
            if command[0].endswith('systemd-run'):
                return response(code=17)
            return response()
        self.assertEqual(API['session'](call), 17)
        launch = next(c for c in calls if c[0].endswith('systemd-run'))
        for option in ('--property=User=gpu', '--property=KillMode=control-group',
                       '--property=Restart=no', '--pty', '--wait'):
            self.assertIn(option, launch)
        self.assertIn(['/usr/bin/systemctl', 'stop', API['UNIT']], calls)

    def test_unknown_or_previous_unit_prevents_launch_without_kill(self):
        for state in ('LoadState=loaded\nDescription=someone else',
                      'LoadState=loaded\nActiveState=active\nDescription=' + DESCRIPTION, ''):
            calls = []
            def call(command, **kwargs):
                calls.append(command)
                return response(state)
            with self.subTest(state=state), self.assertRaises(RuntimeError):
                API['session'](call)
            self.assertEqual(len(calls), 1)

    def test_failed_cleanup_prevents_successful_return(self):
        states = iter(['LoadState=not-found', 'LoadState=loaded\nActiveState=active\nDescription=' + DESCRIPTION,
                       'LoadState=loaded\nActiveState=active\nDescription=' + DESCRIPTION])
        def call(command, **kwargs):
            if command[1] == 'show':
                return response(next(states))
            return response(code=1 if command[1] == 'stop' else 0)
        with self.assertRaisesRegex(RuntimeError, 'cleanup'):
            API['session'](call)
