"""USB documentation copies use Japanese sources without broken local links."""
import hashlib
from pathlib import Path
import re
import runpy
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
API = runpy.run_path(str(ROOT / 'scripts/usb-documents.py'))


class UsbDocumentTests(unittest.TestCase):
    def fixture(self, root):
        (root / 'docs').mkdir()
        texts = {
            'docs/quick-start.md': '# English quick start; do not bundle\n',
            'docs/quick-start.ja.md': '# 日本語の開始手順\n\n[English](quick-start.md)\n\n'
                '[操作](user-guide.md) [build.ja.md](build.ja.md#構築)\n'
                '[同じ文書](#日本語の開始手順) [外部](https://example.invalid/a?q=1#b)\n',
            'docs/user-guide.md': '# 操作\n\n[状態](../TODO.md) [開始](quick-start.ja.md)\n',
            'docs/no-gpu-test.md': '# GPUなし\n',
            'docs/windows-fleet.md': '# 台帳\n\n[開始](quick-start.ja.md)\n',
            'docs/build.md': '# English build; do not bundle\n',
            'docs/build.ja.md': '# 構築\n\n[English](build.md)\n\n'
                '```text\n[English](build.md)\n[code](unbundled.ja.md)\n```\n',
            'TODO.md': '# 状態\n\n[台帳](docs/windows-fleet.md)\n',
        }
        for name, text in texts.items():
            (root / name).write_text(text, encoding='utf-8')
        return texts

    def test_japanese_selection_navigation_links_code_and_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            originals = self.fixture(root)
            plan = API['document_plan'](root)
            self.assertEqual(set(plan), {'QUICK-START.md', 'DETAILS.md', 'NO-GPU-TEST.md',
                                         'WINDOWS-FLEET.md', 'build.md', 'TODO.md'})
            self.assertIn('# 日本語の開始手順', plan['QUICK-START.md'])
            self.assertNotIn('[English]', plan['QUICK-START.md'])
            self.assertNotIn('do not bundle', ''.join(plan.values()))
            self.assertIn('[操作](DETAILS.md)', plan['QUICK-START.md'])
            self.assertIn('[build.md](build.md#構築)', plan['QUICK-START.md'])
            self.assertIn('[状態](TODO.md)', plan['DETAILS.md'])
            self.assertIn('[台帳](WINDOWS-FLEET.md)', plan['TODO.md'])
            self.assertIn('[同じ文書](#日本語の開始手順)', plan['QUICK-START.md'])
            self.assertIn('https://example.invalid/a?q=1#b', plan['QUICK-START.md'])
            self.assertIn('```text\n[English](build.md)\n[code](unbundled.ja.md)\n```', plan['build.md'])
            for name, text in originals.items():
                self.assertEqual((root / name).read_text(encoding='utf-8'), text)

    def test_missing_escape_and_symlink_sources_are_rejected(self):
        for target in ('missing.md', '../../outside.md', 'linked.md', '../.local/private.md'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                self.fixture(root)
                (root / '.local').mkdir()
                (root / '.local/private.md').write_text('synthetic private note; never bundle')
                (root / 'docs/linked.md').symlink_to(root / 'docs/windows-fleet.md')
                (root / 'docs/user-guide.md').write_text('[不適合](' + target + ')\n')
                with self.assertRaises(ValueError):
                    API['document_plan'](root)
                self.assertFalse((root / 'output').exists())
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            (root / 'docs/build.ja.md').unlink()
            (root / 'docs/build.ja.md').symlink_to(root / 'docs/user-guide.md')
            with self.assertRaisesRegex(ValueError, 'symlink'):
                API['document_plan'](root)

    def test_flat_filename_collision_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            (root / 'docs/TODO.md').write_text('# 別の同名文書\n')
            (root / 'docs/user-guide.md').write_text('[a](../TODO.md) [b](TODO.md)\n')
            with self.assertRaisesRegex(ValueError, 'collide'):
                API['document_plan'](root)

    def test_existing_output_and_symlink_directory_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / 'tools'
            output.mkdir()
            existing = output / 'quick-start.md'
            existing.write_text('existing content')
            plan = {'QUICK-START.md': '# 新しい文書\n', 'DETAILS.md': '# 操作\n'}
            with self.assertRaises(ValueError):
                API['write_documents'](output, plan)
            self.assertEqual(existing.read_text(), 'existing content')
            self.assertFalse((output / 'DETAILS.md').exists())
            (root / 'linked').symlink_to(output, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'Ordinary'):
                API['write_documents'](root / 'linked', {'DETAILS.md': '# 操作\n'})

    def test_real_document_bundle_has_closed_links_and_preserves_sources(self):
        paths = [*ROOT.glob('*.md'), *(ROOT / 'docs').glob('*.md')]
        before = {path: hashlib.sha256(path.read_bytes()).digest() for path in paths}
        plan = API['document_plan'](ROOT)
        self.assertTrue(set(API['ENTRY_NAMES'].values()) <= set(plan))
        self.assertEqual(len({name.casefold() for name in plan}), len(plan))
        for name, text in plan.items():
            self.assertNotIn('.ja.md', name)
            # Links inside examples are text, not navigation.
            prose = re.sub(r'^```[^\n]*\n.*?^```', '', text, flags=re.M | re.S)
            self.assertNotRegex(prose, r'\[English(?:\|英語)?\]')
            for _, target in API['LINK'].findall(prose):
                parts = urlsplit(target)
                if not parts.scheme and not parts.netloc and parts.path:
                    self.assertIn(unquote(parts.path), plan, (name, target))
            for path in paths:
                self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before[path])
        with tempfile.TemporaryDirectory() as folder:
            API['write_documents'](Path(folder), plan)
            self.assertEqual({path.name for path in Path(folder).iterdir()}, set(plan))
            for name, text in plan.items():
                self.assertEqual((Path(folder) / name).read_text(encoding='utf-8'), text)
