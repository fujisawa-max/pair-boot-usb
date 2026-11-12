"""GUI collection defaults use a safe subfolder without touching active registries."""
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
WORKER = runpy.run_path(str(ROOT / 'gui/fedora-worker.py'))
GUI = runpy.run_path(str(ROOT / 'gui/fedora-gui.py'))
PWSH = shutil.which('pwsh') or str(ROOT / 'build/powershell-7.5.4/pwsh')


class CollectionDirectoryTests(unittest.TestCase):
    def test_fedora_exports_go_to_data_and_existing_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            mount = Path(folder)
            (mount / 'gpu-config').mkdir()
            active = mount / 'gpu-config/registry.json'
            active.write_bytes(b'synthetic active registry')
            output = WORKER['new_output']('candidate.json', mount)
            self.assertEqual(output, mount / 'data/candidate.json')
            WORKER['write_new'](output, b'synthetic collected JSON')
            self.assertEqual(output.read_bytes(), b'synthetic collected JSON')
            self.assertEqual(active.read_bytes(), b'synthetic active registry')
            with self.assertRaises(ValueError):
                WORKER['new_output']('candidate.json', mount)
            with self.assertRaises(FileExistsError):
                WORKER['write_new'](output, b'never overwrite')
            self.assertEqual(output.read_bytes(), b'synthetic collected JSON')

    def test_bad_names_or_data_directory_links_stop_before_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            mount = Path(folder) / 'usb'; mount.mkdir()
            other = Path(folder) / 'outside'; other.mkdir()
            for name in ('../outside/bad.json', '/bad.json', 'sub/bad.json', 'bad.txt'):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    WORKER['new_output'](name, mount)
            self.assertFalse((mount / 'data').exists())
            (mount / 'data').symlink_to(other, target_is_directory=True)
            with self.assertRaises(ValueError):
                WORKER['new_output']('bad.json', mount)
            self.assertEqual(list(other.iterdir()), [])
            (mount / 'data').unlink()
            (mount / 'data').write_bytes(b'keep this file')
            with self.assertRaises(ValueError):
                WORKER['new_output']('bad.json', mount)
            self.assertEqual((mount / 'data').read_bytes(), b'keep this file')

    def test_fedora_picker_defaults_to_data_but_legacy_root_files_remain_selectable(self):
        picker = GUI['pick_json']
        calls = []
        with tempfile.TemporaryDirectory() as folder:
            mount = Path(folder)
            with patch.dict(picker.__globals__, {'dialog': lambda *args: calls.append(args) or str(mount / 'old.json')}):
                self.assertEqual(picker(str(mount), 'candidate'), str(mount / 'old.json'))
                self.assertIn('--filename=' + str(mount) + '/', calls[-1])
                (mount / 'data').mkdir()
                self.assertEqual(picker(str(mount), 'candidate'), str(mount / 'old.json'))
                self.assertIn('--filename=' + str(mount / 'data') + '/', calls[-1])

    @unittest.skipUnless(Path(PWSH).is_file(), 'Optional fixed PowerShell fixture runner needed')
    def test_windows_default_directory_creates_data_and_rejects_aliases(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as folder:
            base = Path(folder)
            usb = base / 'usb'; usb.mkdir()
            bad = base / 'bad'; bad.mkdir()
            (bad / 'data').symlink_to(usb, target_is_directory=True)
            script = base / 'paths.ps1'
            helper = str(ROOT / 'gui/storage-paths.ps1').replace("'", "''")
            script.write_text("param($Good,$Bad)\n$ErrorActionPreference='Stop'\n. '" + helper + "'\n"
                              "$path=Get-CollectionDirectory $Good\n"
                              "if ($path -ne (Join-Path $Good 'data')) {throw 'Wrong default'}\n"
                              "$again=Get-CollectionDirectory $Good\n"
                              "try {Get-CollectionDirectory $Bad;throw 'Accepted symlink'} catch {if($_.Exception.Message -eq 'Accepted symlink'){throw}}\n"
                              "Write-Output $path\n")
            result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File', str(script),
                                     '-Good', str(usb), '-Bad', str(bad)], capture_output=True, text=True,
                                    env={**os.environ, 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
                                         'POWERSHELL_TELEMETRY_OPTOUT': '1',
                                         'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
                                         'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((usb / 'data').is_dir())
