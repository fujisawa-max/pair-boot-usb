"""Validate newc hardlinks as emitted by dracut, without unpacking archives."""
from pathlib import Path
import runpy
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
READ = runpy.run_path(str(ROOT / 'scripts/validate-appliance.py'))['cpio_entries']


def archive(members):
    result = bytearray()
    for name, payload, inode in [*members, ('TRAILER!!!', b'', 0)]:
        name = name.encode() + b'\0'
        fields = (inode, 0o100644, 0, 0, 2, 0, len(payload), 1, 2, 0, 0, len(name), 0)
        result += b'070701' + ''.join(f'{item:08x}' for item in fields).encode() + name
        result += b'\0' * (-len(result) % 4)
        result += payload
        result += b'\0' * (-len(result) % 4)
    return result


class InitramfsHardlinkTests(unittest.TestCase):
    def test_hardlink_alias_resolves_to_data_before_or_after_its_header(self):
        for members in ([('loader', b'', 7), ('sec2', b'firmware', 7)],
                        [('loader', b'firmware', 7), ('sec2', b'', 7)]):
            with self.subTest(members=members), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'initrd'; path.write_bytes(archive(members))
                data, entries = READ(path)
                for name in ('loader', 'sec2'):
                    begin, size = entries[name]
                    self.assertEqual(data[begin:begin+size], b'firmware')

    def test_inode_reuse_in_appended_archive_does_not_change_previous_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'initrd'
            path.write_bytes(archive([('old', b'first', 7), ('old-link', b'', 7)]) +
                             archive([('new', b'second', 7), ('new-link', b'', 7)]))
            data, entries = READ(path)
            for name, expected in (('old', b'first'), ('old-link', b'first'), ('new', b'second'), ('new-link', b'second')):
                offset, size = entries[name]
                self.assertEqual(data[offset:offset+size], expected)

    def test_conflicting_contents_and_truncated_headers_are_rejected(self):
        for payload in (archive([('one', b'first', 7), ('two', b'other', 7)]), b'070701'):
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'initrd'; path.write_bytes(payload)
                with self.assertRaises(ValueError):
                    READ(path)
