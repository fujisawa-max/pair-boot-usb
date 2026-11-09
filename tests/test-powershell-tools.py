"""Fixture runtime preparation rejects corrupt archives and unsafe output paths."""
import io
from pathlib import Path
import runpy
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PREPARE = runpy.run_path(str(ROOT / 'scripts/prepare-powershell-tests.py'))


class PowerShellPreparationTests(unittest.TestCase):
    def test_ordinary_files_extract_but_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            archive = base / 'tool.tar.gz'
            with tarfile.open(archive, 'w:gz') as target:
                item = tarfile.TarInfo('pwsh'); item.size = 3; item.mode = 0o755
                target.addfile(item, io.BytesIO(b'abc'))
            output = base / 'output'
            PREPARE['extract'](archive, output)
            self.assertEqual((output / 'pwsh').read_bytes(), b'abc')
            with self.assertRaises(ValueError):
                PREPARE['extract'](archive, output)
            self.assertEqual((output / 'pwsh').read_bytes(), b'abc')

    def test_links_escape_case_collisions_corruption_and_parent_alias_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            archive = base / 'tool.tar.gz'
            output = base / 'output'
            for name, kind in (('../escape', tarfile.REGTYPE), ('/escape', tarfile.REGTYPE),
                               ('a\\..\\escape', tarfile.REGTYPE), ('C:escape', tarfile.REGTYPE),
                               ('link', tarfile.SYMTYPE), ('hard', tarfile.LNKTYPE)):
                with self.subTest(name=name):
                    with tarfile.open(archive, 'w:gz') as target:
                        item = tarfile.TarInfo(name); item.type = kind; item.linkname = 'outside'
                        target.addfile(item)
                    with self.assertRaises(ValueError):
                        PREPARE['extract'](archive, output)
                    self.assertFalse(output.exists())
            with tarfile.open(archive, 'w:gz') as target:
                target.addfile(tarfile.TarInfo('File'))
                target.addfile(tarfile.TarInfo('file'))
            with self.assertRaises(ValueError):
                PREPARE['extract'](archive, output)
            archive.write_bytes(b'corrupt fixture')
            with self.assertRaises(tarfile.TarError):
                PREPARE['extract'](archive, output)
            alias = base / 'alias'; alias.symlink_to(base, target_is_directory=True)
            with self.assertRaises(ValueError):
                PREPARE['extract'](archive, alias / 'new')
            self.assertFalse(output.exists())
