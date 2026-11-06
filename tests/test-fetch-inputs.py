"""Input acquisition failures use synthetic bytes and never access the network."""
import hashlib
import io
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FETCH = runpy.run_path(str(ROOT / 'scripts/fetch-inputs.py'))


class FetchInputsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = b'synthetic package bytes'
        self.entry = {'file': '.cache/test/input.deb',
                      'url': 'https://deb.debian.org/debian/pool/main/test.deb',
                      'sha256': hashlib.sha256(self.data).hexdigest(),
                      'size': len(self.data)}

    def response(self, data=None):
        stream = io.BytesIO(self.data if data is None else data)
        stream.url = self.entry['url']
        return stream

    def test_download_and_offline_reuse(self):
        status = FETCH['fetch'](self.entry, self.root, opener=lambda *a, **k: self.response())
        self.assertEqual(status, 'downloaded')
        self.assertEqual((self.root / self.entry['file']).read_bytes(), self.data)
        self.assertEqual(FETCH['fetch'](self.entry, self.root, offline=True), 'cached')
        self.assertEqual(list((self.root / '.cache/test').glob('.input-*')), [])

    def test_missing_offline_does_not_create_cache(self):
        with self.assertRaisesRegex(ValueError, 'Missing offline'):
            FETCH['fetch'](self.entry, self.root, offline=True)
        self.assertFalse((self.root / '.cache').exists())

    def test_corrupt_cache_is_preserved_without_network(self):
        path = self.root / self.entry['file']
        path.parent.mkdir(parents=True)
        path.write_bytes(b'x' * len(self.data))
        def unexpected(*args, **kwargs):
            self.fail('Corrupt cache must not trigger a download')
        with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
            FETCH['fetch'](self.entry, self.root, opener=unexpected)
        self.assertEqual(path.read_bytes(), b'x' * len(self.data))

    def test_bad_download_never_publishes_cache(self):
        for payload in (b'short', b'x' * len(self.data), self.data + b'extra'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                FETCH['fetch'](self.entry, self.root, opener=lambda *a, **k: self.response(payload))
            self.assertFalse((self.root / self.entry['file']).exists())
            self.assertEqual(list((self.root / '.cache/test').iterdir()), [])

    def test_network_failure_cleans_temporary_file(self):
        def failed(*args, **kwargs):
            raise OSError('synthetic interrupted download')
        with self.assertRaises(OSError):
            FETCH['fetch'](self.entry, self.root, opener=failed)
        self.assertEqual(list((self.root / '.cache/test').iterdir()), [])

    def test_symlink_file_and_parent_are_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / '.cache').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            FETCH['fetch'](self.entry, self.root, offline=True)
        (self.root / '.cache').unlink()
        path = self.root / self.entry['file']
        path.parent.mkdir(parents=True)
        path.symlink_to(outside / 'absent')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            FETCH['fetch'](self.entry, self.root, offline=True)

    def test_escape_paths_are_rejected(self):
        for relative in ('/tmp/other', '.cache/../../other', 'vendor/input'):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                FETCH['cache_path'](self.root, relative)

    def test_non_https_redirect_rejected(self):
        with self.assertRaisesRegex(ValueError, 'non-HTTPS'):
            FETCH['HTTPSRedirects']().redirect_request(None, None, 302, '', {}, 'http://example.invalid/input')

    def test_plan_conflicts_fail_before_download(self):
        inputs = self.root / 'inputs'
        inputs.mkdir()
        package = {**self.entry, 'filename': 'test.deb'}
        (inputs / 'debian-build-inputs.json').write_text(json.dumps({'packages': [package]}))
        package['sha256'] = '0' * 64
        (inputs / 'debian-runtime-inputs.json').write_text(json.dumps({'packages': [package]}))
        with self.assertRaisesRegex(ValueError, 'Conflicting locks'):
            FETCH['input_plan'](['debian-build', 'debian-runtime'], self.root)

    def test_all_committed_locks_have_valid_plan(self):
        records = FETCH['input_plan'](list(FETCH['GROUPS']))
        self.assertGreater(len(records), 0)
        self.assertEqual(len(records), len({item['file'] for item in records}))

    def test_network_missing_inputs_creates_no_output_and_preserves_lock(self):
        scripts = self.root / 'scripts'
        scripts.mkdir()
        (scripts / 'fetch-inputs.py').write_text((ROOT / 'scripts/fetch-inputs.py').read_text())
        inputs = self.root / 'inputs'
        inputs.mkdir()
        lock = inputs / 'candidate-network-inputs.json'
        lock.write_text((ROOT / 'inputs/candidate-network-inputs.json').read_text())
        original = lock.read_bytes()
        script = ROOT / 'scripts/prepare-candidate-network.py'
        module = runpy.run_path(str(script))
        # main's globals belong to the function created by run_path.
        module['main'].__globals__['ROOT'] = self.root
        with patch('sys.argv', [str(script)]), self.assertRaisesRegex(ValueError, 'Missing offline'):
            module['main']()
        self.assertFalse((self.root / 'build').exists())
        self.assertEqual(lock.read_bytes(), original)
