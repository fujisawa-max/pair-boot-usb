"""Unsupported Python must stop before test discovery or build-side effects."""
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BUILD = runpy.run_path(str(ROOT / 'scripts/build-usb.py'))


class PythonPrerequisiteTests(unittest.TestCase):
    def test_python_310_stops_test_runner_before_discovery(self):
        with (patch('sys.version_info', (3, 10, 12)),
              patch('importlib.util.spec_from_file_location') as discover):
            with self.assertRaises(SystemExit) as caught:
                runpy.run_path(str(ROOT / 'scripts/run-tests.py'))
            self.assertIn('Python 3.11 or newer', str(caught.exception))
            self.assertIn('3.10.12', str(caught.exception))
            self.assertIn('No tests were executed', str(caught.exception))
            discover.assert_not_called()

    def test_python_310_stops_build_before_tool_and_disk_checks(self):
        with (patch('sys.version_info', (3, 10, 12)),
              patch('shutil.which') as which, patch('shutil.disk_usage') as disk):
            with self.assertRaisesRegex(ValueError, r'Python >=3.11.*3.10.12'):
                BUILD['check_host']()
            which.assert_not_called()
            disk.assert_not_called()

    def test_python_311_is_accepted_when_other_prerequisites_exist(self):
        with (patch('sys.version_info', (3, 11, 0)),
              patch('shutil.which', return_value='/fixture/tool'),
              patch('shutil.disk_usage') as disk):
            disk.return_value.free = 64 * 1024**3
            BUILD['check_host']()

    def test_prepare_uses_selected_python_for_python_subprocesses(self):
        executable = '/fixture/conda/envs/pair-boot-build/bin/python'
        with patch('sys.executable', executable):
            commands = BUILD['prepare_commands']()
        python_commands = [command for command in commands if any(value.endswith('.py') for value in command)]
        self.assertTrue(python_commands)
        self.assertTrue(all(command[0] == executable for command in python_commands))
