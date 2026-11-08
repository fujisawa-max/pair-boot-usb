"""Banner reads the running release and mode; no terminal control in plain output."""
import io
import json
from pathlib import Path
import re
import runpy
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BANNER = runpy.run_path(str(ROOT / 'runtime/boot-banner.py'))
CONSOLE = runpy.run_path(str(ROOT / 'runtime/pair-console.py'))


class BannerTests(unittest.TestCase):
    def test_version_mode_colors_and_100_by_30_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image, state = root / 'image', root / 'state'
            image.write_text(json.dumps({'version': '0.9.7-shared'}))
            state.write_text(json.dumps({'mode': 'Unlock'}))
            value = BANNER['render'](image, state, color=True)
            self.assertIn('\033[96m', value)
            self.assertIn('\033[92m', value)
            self.assertIn('0.9.7-shared', value)
            self.assertIn('Mode: Unlock', value)
            plain = re.sub(r'\x1b\[[0-9;]*m', '', value)
            self.assertEqual(len(plain.splitlines()), 15)
            self.assertLessEqual(max(map(len, plain.splitlines())), 100)
            image.write_text('{"version":"0.9.8-shared"}')
            self.assertIn('0.9.8', BANNER['render'](image, state))

    def test_bad_metadata_cannot_inject_terminal_controls(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / 'image'
            state = Path(folder) / 'state'
            image.write_text(json.dumps({'version': '\033[2J'}))
            state.write_text('{"mode":"invented"}')
            value = BANNER['render'](image, state)
            self.assertNotIn('\033', value)
            self.assertIn('Version: unknown', value)
            self.assertIn('Mode: unknown', value)

    def test_banner_is_repeated_after_pair_exit(self):
        output = io.StringIO()
        CONSOLE['menu']('notice', io.StringIO('1\n'), output, lambda: 0)
        self.assertEqual(output.getvalue().count('Version:'), 2)
        self.assertNotIn('\033', output.getvalue())
