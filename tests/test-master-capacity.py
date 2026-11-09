"""Sparse distribution templates fit a smaller master without weakening bounds."""
import argparse
import errno
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ASSEMBLE = runpy.run_path(str(ROOT / 'scripts/assemble-usb-image.py'))
BUILD = runpy.run_path(str(ROOT / 'scripts/build-usb.py'))
MIB = 1024**2


class MasterCapacityTests(unittest.TestCase):
    def test_dense_and_sparse_files_use_data_extents_without_changing_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            tree = Path(folder)
            dense = tree / 'dense'; dense.write_bytes(b'x' * 8192)
            sparse = tree / 'sparse'
            with sparse.open('wb') as stream:
                stream.write(b'first')
                stream.seek(64 * MIB - 4)
                stream.write(b'last')
            usage = ASSEMBLE['payload_usage'](tree)
            self.assertEqual(usage['logical_bytes'], 64 * MIB + 8192)
            self.assertGreaterEqual(usage['data_extent_bytes'], 8192 + 8)
            self.assertLess(usage['data_extent_bytes'], usage['logical_bytes'])
            with sparse.open('rb') as stream:
                self.assertEqual(stream.read(5), b'first')
                stream.seek(-4, os.SEEK_END)
                self.assertEqual(stream.read(), b'last')

    def test_unknown_hole_mapping_uses_full_size_and_io_errors_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sparse'
            with path.open('wb') as stream:
                stream.truncate(16 * MIB)
            for error in (errno.EINVAL, errno.ENOTSUP):
                with patch('os.lseek', side_effect=OSError(error, 'fixture')):
                    self.assertEqual(ASSEMBLE['sparse_bytes'](path), 16 * MIB)
            with patch('os.lseek', side_effect=[0, OSError(errno.ENXIO, 'fixture')]):
                self.assertEqual(ASSEMBLE['sparse_bytes'](path), 16 * MIB)
            with patch('os.lseek', side_effect=OSError(errno.EIO, 'fixture')):
                with self.assertRaises(OSError):
                    ASSEMBLE['sparse_bytes'](path)

    def test_links_and_special_files_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            tree = Path(folder)
            ordinary = tree / 'ordinary'; ordinary.write_bytes(b'fixture')
            link = tree / 'alias'; link.symlink_to(ordinary)
            with self.assertRaises(ValueError):
                ASSEMBLE['payload_usage'](tree)
            with self.assertRaises(ValueError):
                ASSEMBLE['payload_usage'](link)
            link.unlink()
            os.mkfifo(tree / 'pipe')
            with self.assertRaises(ValueError):
                ASSEMBLE['payload_usage'](tree)

    def test_insufficient_capacity_stops_before_creating_output_or_running_tools(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as folder:
            base = Path(folder); tree = base / 'tree'; (tree / 'gpu-linux').mkdir(parents=True)
            (tree / 'gpu-linux/master.json').write_text(json.dumps({'version': 'fixture'}))
            (tree / 'payload').write_bytes(b'x' * 8192)
            output = base / 'output'
            args = ['assemble-usb-image.py', '--tree', str(tree), '--sysroot', str(tree),
                    '--output', str(output), '--data-mib', '512']
            with patch('sys.argv', args), patch('subprocess.run') as run:
                with self.assertRaises(SystemExit) as error:
                    ASSEMBLE['main']()
                self.assertEqual(error.exception.code, 2)
                run.assert_not_called()
            self.assertFalse(output.exists())
            self.assertEqual((tree / 'payload').read_bytes(), b'x' * 8192)

    def test_actual_free_space_reserve_and_invalid_statistics_are_enforced(self):
        good = 'Block count: 1048576\nBlock size: 4096\nFree blocks: 200000\nReserved block count: 1000\n'
        self.assertEqual(ASSEMBLE['master_available'](good), 199000 * 4096)
        for text in (good.replace('200000', '100000'),
                     good.replace('Block size: 4096\n', ''),
                     good + 'Free blocks: 200000\n',
                     good.replace('1000\n', '300000\n')):
            with self.subTest(text=text), self.assertRaises(ValueError):
                ASSEMBLE['master_available'](text)

    def test_build_entry_and_assembler_share_the_4608_mib_default(self):
        args = argparse.Namespace(signed_modules=Path('build/signed'), appliance_output=Path('build/appliance'),
                                  tools_output=Path('build/tools'), fedora_output=Path('build/fedora'),
                                  output=Path('build/image'))
        commands = BUILD['image_commands'](args)
        assemble, = [cmd for cmd in commands if cmd[1].endswith('/assemble-usb-image.py')]
        self.assertEqual(assemble[assemble.index('--data-mib') + 1], '4608')
        self.assertEqual(ASSEMBLE['DEFAULT_MASTER_MIB'], 4608)
