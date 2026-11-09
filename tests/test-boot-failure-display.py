"""Visible boot failure reasons precede each synthetic maintenance prompt."""
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOOT = runpy.run_path(str(ROOT / 'runtime/gpu-root.py'))


class BootFailureDisplayTests(unittest.TestCase):
    def test_registration_and_mode_errors_include_reason_and_next_action(self):
        for reason, title, action in (
                ('This PC is not registered; use Fedora fleet-register first',
                 'THIS PC IS NOT REGISTERED FOR THIS USB', 'Boot Fedora Live'),
                ('No-GPU test is restricted to PCs without NVIDIA hardware',
                 'NVIDIA HARDWARE DETECTED IN TEST MODE', 'Normal or Unlock'),
                ('Boot menu mode and registered PC mode differ',
                 'BOOT MODE DOES NOT MATCH THE PC REGISTRATION', 'Choose TEST'),
                ('gpu_unlock was not loaded before storage',
                 'GPU MODULE COULD NOT LOAD', 'MOK'),
                ('fixture storage mismatch', 'ROOT SETUP FAILED', 'inspect')):
            with self.subTest(reason=reason):
                text = BOOT['boot_failure'](RuntimeError(reason))
                self.assertTrue(text.startswith('\n\n' + '=' * 70))
                self.assertTrue(text.endswith('\n\n'))
                self.assertIn(title, text)
                self.assertIn(action, text)
                self.assertIn('Details:', text)
                self.assertIn(reason, text)
                self.assertLessEqual(max(map(len, text.splitlines())), 70)

    def test_original_reason_is_repeated_before_each_maintenance_prompt(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            error = base / 'boot-error.txt'
            reason = 'This PC is not registered; use Fedora fleet-register first'
            error.write_text(BOOT['boot_failure'](RuntimeError(reason)))
            hook = base / 'hook.sh'
            source = (ROOT / 'runtime/dracut/95gpu-root/mount-root.sh').read_text()
            hook.write_text(source.replace('/run/gpu-root-boot-error.txt', str(error)))
            # No devices, boot controller, or real maintenance shell are invoked.
            shell = ('root=fixture; count=0; warn() { :; }; sleep() { :; }; '
                     'emergency_shell() { count=$((count+1)); echo MAINTENANCE_PROMPT; '
                     '[ "$count" -lt 2 ] || exit 23; }; . "$1"; gpu_root_stop fixture')
            result = subprocess.run(['sh', '-c', shell, 'fixture', str(hook)],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self.assertEqual(result.returncode, 23)
            self.assertEqual(result.stdout.count('THIS PC IS NOT REGISTERED'), 2)
            sections = result.stdout.split('MAINTENANCE_PROMPT')
            self.assertIn(reason, sections[0])
            self.assertIn(reason, sections[1])
