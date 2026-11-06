"""Fresh-clone checks and package installation with synthetic host commands."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CHECK = runpy.run_path(str(ROOT / 'scripts/check-environment.py'))
INSTALL = ROOT / 'scripts/install-build-dependencies.sh'


class BuildEnvironmentTests(unittest.TestCase):
    def inspect(self, root, **kwargs):
        return CHECK['inspect_environment'](root, python_version=(3, 12, 0),
                                            system='Linux', machine='x86_64', **kwargs)

    def test_fresh_clone_does_not_need_vendor_or_target_headers(self):
        with (tempfile.TemporaryDirectory() as directory,
              patch('shutil.which', return_value='/fixture/tool'),
              patch('shutil.disk_usage') as disk):
            disk.return_value.free = 64 * 1024**3
            report = self.inspect(Path(directory))
            self.assertTrue(report['build_prerequisites_present'], report)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_missing_commands_explain_packages_and_remediation(self):
        missing = {'bwrap', 'fakeroot', 'zstd'}
        with (patch('shutil.which', side_effect=lambda name: None if name in missing else '/fixture/tool'),
              patch('shutil.disk_usage') as disk):
            disk.return_value.free = 128 * 1024**3
            report = self.inspect(ROOT)
        self.assertFalse(report['build_prerequisites_present'])
        self.assertEqual(report['missing_packages'], ['bubblewrap', 'fakeroot', 'zstd'])
        self.assertIn('install-build-dependencies.sh', ' '.join(report['missing']))

    def test_insufficient_storage_is_rejected(self):
        with (patch('shutil.which', return_value='/fixture/tool'),
              patch('shutil.disk_usage') as disk):
            disk.return_value.free = 64 * 1024**3 - 1
            report = self.inspect(ROOT)
        self.assertFalse(report['build_prerequisites_present'])
        self.assertIn('64 GiB', ' '.join(report['missing']))

    def test_unsupported_platforms_are_rejected(self):
        with (patch('shutil.which', return_value='/fixture/tool'),
              patch('shutil.disk_usage') as disk):
            disk.return_value.free = 128 * 1024**3
            for system, machine in [('Darwin', 'x86_64'), ('Linux', 'aarch64'), ('Windows', 'AMD64')]:
                with self.subTest(system=system, machine=machine):
                    report = CHECK['inspect_environment'](ROOT, python_version=(3, 12, 0),
                                                           system=system, machine=machine)
                    self.assertFalse(report['build_prerequisites_present'])
                    self.assertIn('x86_64 Linux', ' '.join(report['missing']))

    def test_python_310_can_report_installer_packages_without_checks(self):
        with (patch('sys.version_info', (3, 10, 12)),
              patch('sys.argv', ['check-environment.py', '--apt-packages']),
              patch('shutil.which') as which, patch('shutil.disk_usage') as disk,
              patch('builtins.print') as output):
            self.assertEqual(CHECK['main'](), 0)
            self.assertIn('bubblewrap', output.call_args.args[0].split())
            which.assert_not_called()
            disk.assert_not_called()


class DependencyInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.log = self.directory / 'calls.jsonl'
        self.write_command('id', '#!/bin/sh\nprintf "1000\\n"\n')
        self.write_command('apt-get', '#!/bin/sh\nexit 99\n')
        self.write_command('sudo', '#!' + sys.executable + '\nimport json, os, sys\n'
                           'with open(os.environ["INSTALL_TEST_LOG"], "a") as stream:\n'
                           '    stream.write(json.dumps(sys.argv[1:]) + "\\n")\n')
        self.env = dict(os.environ, PATH=str(self.directory) + os.pathsep + os.environ['PATH'],
                        PYTHON=sys.executable, INSTALL_TEST_LOG=str(self.log))

    def write_command(self, name, text):
        path = self.directory / name
        path.write_text(text)
        path.chmod(0o755)

    def run_installer(self, *args):
        return subprocess.run(['sh', str(INSTALL), *args], env=self.env,
                              capture_output=True, text=True)

    def test_print_mode_only_lists_packages(self):
        result = self.run_installer('--print')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.split()), set(CHECK['APT_PACKAGES']))
        self.assertFalse(self.log.exists())

    def test_install_only_runs_expected_apt_commands(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(calls, [['apt-get', 'update'],
                                ['apt-get', 'install', '-y', '--no-install-recommends',
                                 *CHECK['APT_PACKAGES']]])
        self.assertIn('OS python3 package may be older', result.stdout)

    def test_invalid_argument_does_not_install(self):
        result = self.run_installer('--unexpected')
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.log.exists())

    def test_root_user_is_rejected_before_install(self):
        self.write_command('id', '#!/bin/sh\nprintf "0\\n"\n')
        result = self.run_installer()
        self.assertEqual(result.returncode, 1)
        self.assertIn('normal user', result.stderr)
        self.assertFalse(self.log.exists())
