"""Registered names affect only the runtime hostname on synthetic state files."""
import json
from pathlib import Path
import re
import runpy
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = runpy.run_path(str(ROOT / 'runtime/initialize-identity.py'))
HARDWARE = '01234567-89ab-cdef-0123-456789abcdef'
FALLBACK = 'gpu-112233445566'


class PairHostnameTests(unittest.TestCase):
    def test_ascii_names_are_normalized_and_keep_the_full_hardware_uuid(self):
        for source, prefix in (('LLM-PC-01', 'llm-pc-01'), ('Lab PC_01', 'lab-pc-01'),
                               ('ＧＰＵ-Café', 'gpu-cafe'), ('推論専用', 'gpu')):
            with self.subTest(source=source):
                name = IDENTITY['runtime_hostname'](HARDWARE, source, FALLBACK)
                self.assertEqual(name, prefix + '-0123456789abcdef0123456789abcdef')
                self.assertRegex(name, r'^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$')
                self.assertLessEqual(len(name), 63)
        long_name = IDENTITY['runtime_hostname'](HARDWARE, 'A' * 255, FALLBACK)
        self.assertEqual(len(long_name), 63)
        self.assertTrue(long_name.endswith('0123456789abcdef0123456789abcdef'))

    def test_missing_legacy_name_keeps_existing_gpu_name(self):
        self.assertEqual(IDENTITY['runtime_hostname'](HARDWARE, None, FALLBACK), FALLBACK)

    def test_identical_name_and_uuid_prefix_do_not_collapse_distinct_full_uuids(self):
        a = IDENTITY['runtime_hostname'](HARDWARE, 'LLM-PC', FALLBACK)
        b = IDENTITY['runtime_hostname']('01234567-89ab-cdef-0123-456789abcdee', 'LLM-PC', FALLBACK)
        self.assertNotEqual(a, b)

    def test_invalid_names_identity_or_fallback_stop(self):
        for value in ('', ' bad', 'bad ', 'bad\nname', 'x\x00y', 'x\u202ey', 'x' * 256, False, 7):
            with self.subTest(value=value), self.assertRaises(ValueError):
                IDENTITY['runtime_hostname'](HARDWARE, value, FALLBACK)
        with self.assertRaises(ValueError):
            IDENTITY['runtime_hostname']('not-a-uuid', 'LLM-PC', FALLBACK)
        with self.assertRaises(ValueError):
            IDENTITY['runtime_hostname'](HARDWARE, 'LLM-PC', 'unexpected')

    def test_legacy_state_and_pair_settings_are_unchanged_across_renames(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'etc').mkdir()
            (root / 'etc/machine-id').write_text('')
            info = IDENTITY['initialize'](root, HARDWARE, lambda: None)
            app = root / 'home/gpu/.config/Nvidia Corporation/Personal AI Router'
            app.mkdir(parents=True)
            (app / 'node-id.json').write_text('{"node_uuid":"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}')
            (app / 'settings.json').write_text('{"synthetic_user_settings":true}')
            original = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            applied = []
            for managed, expected in (({}, info['hostname']),
                                      ({'windows_computer_name': 'LLM-PC'}, 'llm-pc-0123456789abcdef0123456789abcdef'),
                                      ({'windows_computer_name': 'NEW-PC'}, 'new-pc-0123456789abcdef0123456789abcdef'),
                                      ({'windows_computer_name': None}, info['hostname'])):
                actual = IDENTITY['apply_hostname'](root, HARDWARE, managed,
                    lambda: self.fail('Existing identity must not regenerate keys'), applied.append)
                self.assertEqual(actual, expected)
                self.assertEqual(applied[-1], expected)
                self.assertEqual({str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}, original)
            with self.assertRaisesRegex(ValueError, 'another PC'):
                IDENTITY['apply_hostname'](root, 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', {}, lambda: None, applied.append)


if __name__ == '__main__':
    unittest.main()
