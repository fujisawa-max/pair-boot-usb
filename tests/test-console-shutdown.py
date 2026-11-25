"""Exercise common root console policy without booting or invoking systemctl."""
from pathlib import Path
import runpy
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILD = runpy.run_path(str(ROOT / 'scripts/build-root-appliance.py'))
MENU = runpy.run_path(str(ROOT / 'scripts/generate-boot-menu.py'))


class ConsolePolicyTests(unittest.TestCase):
    def test_common_root_masks_console_and_emergency_shell_units(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            BUILD['mask_console_logins'](stage)
            units = stage / 'etc/systemd/system'
            for name in ('gpu-root-console.service', 'getty@.service', 'serial-getty@.service',
                         'console-getty.service', 'container-getty@.service', 'debug-shell.service',
                         'emergency.service', 'rescue.service'):
                self.assertEqual((units / name).readlink(), Path('/dev/null'))
            self.assertFalse((units / 'gpu-pair.service').exists())

    def test_all_boot_menu_modes_disable_the_initramfs_shell(self):
        for menu in (MENU['generate']('abcd-1234', {}, shared_registry=True),
                     MENU['generate_diagnostic']('abcd-1234')):
            entries = [line.split() for line in menu.splitlines() if line.strip().startswith('linux ')]
            self.assertTrue(entries)
            for args in entries:
                self.assertIn('rd.shell=0', args)
                self.assertIn('rd.systemd.mask=debug-shell.service', args)
