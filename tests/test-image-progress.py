"""Progress and transaction failures use ordinary temporary files only."""
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import runpy
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('progress_api', ROOT / 'runtime/gpu-root.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
STATE = runpy.run_path(str(ROOT / 'runtime/persistent-state.py'))
REPLACE = runpy.run_path(str(ROOT / 'runtime/replace-root.py'))


class ImageProgressTests(unittest.TestCase):
    def test_boot_hook_sends_progress_to_console_and_keeps_error_details(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            console, errors = root / 'console', root / 'errors'
            python = root / 'fixture-python'
            python.write_text('#!/bin/sh\nprintf "fixture progress\\n"\nprintf "fixture detail\\n" >&2\n')
            python.chmod(0o755)
            source = (ROOT / 'runtime/dracut/95gpu-root/mount-root.sh').read_text()
            source = source.replace('/usr/bin/python3', shlex.quote(str(python)))
            source = source.replace('/dev/console', shlex.quote(str(console)))
            source = source.replace('/run/gpu-root-boot-error.txt', shlex.quote(str(errors)))
            stubs = 'modprobe() { :; }; ismounted() { return 1; }; need_shutdown() { :; }; root=gpu-root\n'
            result = subprocess.run(['sh', '-c', stubs + source], check=True,
                                    text=True, capture_output=True)
            self.assertEqual(console.read_text(), 'fixture progress\n')
            self.assertEqual(errors.read_text(), 'fixture detail\n')
            self.assertEqual(result.stdout, '')
            self.assertEqual(result.stderr, '')

    def test_console_has_step_totals_bytes_and_no_time_estimate(self):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            progress = api.ImageProgress('Fixture copy', 2)
            with progress.step('Copying root.img', 5 * 1024**3):
                progress.advance(5 * 1024**3 // 2)
                progress.render()
                progress.advance(5 * 1024**3 // 2)
            with progress.step('Flushing root.img to storage'):
                pending = output.getvalue().split('[step 2/2]')[1]
                self.assertIn('Working; please wait...', pending)
                self.assertNotIn('%', pending)
                self.assertNotIn('Done.', pending)
        text = output.getvalue()
        self.assertIn('[step 1/2] Copying root.img', text)
        self.assertIn('[##########----------]  50% (2.5 / 5.0 GiB)', text)
        self.assertNotRegex(text.lower(), r'eta|seconds|remaining')
        self.assertEqual(errors.getvalue(), '')
        self.assertNotIn('\r', text)

    def test_tty_throttles_updates_and_ends_failed_line_without_success(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        output = Terminal()
        with patch.object(api.time, 'monotonic', return_value=10):
            progress = api.ImageProgress('Fixture hash', 1, output)
            with self.assertRaisesRegex(OSError, 'fixture interruption'):
                with progress.step('Verifying root.img', 100):
                    for _ in range(52):
                        progress.advance(1)
                    raise OSError('fixture interruption')
        text = output.getvalue()
        self.assertEqual(text.count('\r'), 1)
        self.assertIn('\n  Failed or interrupted;', text)
        self.assertNotIn('Done.', text)
        self.assertNotIn('100%', text)

    def test_copy_and_hash_count_all_bytes_including_final_short_chunk(self):
        data = b'a' * (8 * 1024**2 + 7)
        output = io.BytesIO()
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            source = Path(folder) / 'source.img'
            source.write_bytes(data)
            progress = api.ImageProgress('Fixture copy and hash', 2)
            hooks = []
            with progress.step('Copy', len(data)):
                with source.open('rb') as stream:
                    api.copy_image(stream, output, progress, lambda out: hooks.append(out.tell()))
                self.assertEqual(progress.current, len(data))
            with progress.step('Hash', len(data)):
                self.assertEqual(api.digest(source, progress), hashlib.sha256(data).hexdigest())
                self.assertEqual(progress.current, len(data))
            self.assertEqual(output.getvalue(), data)
            self.assertEqual(hooks, [8 * 1024**2, len(data)])


class ImageTransactionProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.parent = self.root / 'storage'
        self.parent.mkdir()
        self.master = self.root / 'master.img'
        with self.master.open('wb') as output:
            output.truncate(32 * 1024**2)
        subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-F', str(self.master)],
                       check=True, capture_output=True)
        self.manifest = {'version': 'fixture', 'size': self.master.stat().st_size,
                         'sha256': api.digest(self.master)}

    def provision(self, **kwargs):
        return api.provision(self.master, self.parent, self.manifest,
                             free_bytes=2 * 1024**3, **kwargs)

    def assert_steps(self, text, operation, labels):
        segment = text.split('PAIR BOOT: ' + operation + ' (5 steps)', 1)[1]
        # A first root creation can include the separate state creation sequence.
        stages = re.findall(r'\[step (\d)/5\] (.*)', segment)
        expected = list(enumerate(labels, 1))
        self.assertEqual([(int(n), label) for n, label in stages], expected)

    def test_initial_root_and_state_have_separate_complete_sequences(self):
        output = io.StringIO()
        def state():
            STATE['create'](api, self.master, self.parent, self.manifest, 'fixture-PC',
                            free_bytes=2 * 1024**3)
        with redirect_stdout(output):
            target, created = self.provision(prepare_new=state)
        self.assertTrue(created)
        self.assertEqual(api.digest(target), self.manifest['sha256'])
        self.assertEqual(api.digest(self.parent / 'state.img'), self.manifest['sha256'])
        text = output.getvalue()
        root_copy, rest = text.split('PAIR BOOT: Creating state.img')
        root_finish = rest[rest.index('[step 5/5] Finalizing root.img'):]
        state_copy = 'PAIR BOOT: Creating state.img' + rest[:rest.index('[step 5/5] Finalizing root.img')]
        self.assert_steps(root_copy + root_finish, 'Creating root.img', [
            'Verifying root.img source', 'Copying root.img', 'Flushing root.img to storage',
            'Verifying written root.img', 'Finalizing root.img'])
        self.assert_steps(state_copy, 'Creating state.img', [
            'Verifying state.img source', 'Copying state.img', 'Flushing state.img to storage',
            'Verifying written state.img', 'Finalizing state.img'])
        self.assertIn('100%', text)

    def test_corrupt_copy_is_not_published_or_reported_as_finished(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaisesRegex(api.SafetyError, 'Copy size/SHA'):
            self.provision(copy_hook=lambda stream: stream.write(b'corruption'))
        self.assertFalse((self.parent / 'root.img').exists())
        self.assertFalse((self.parent / 'root-image.json').exists())
        self.assertTrue((self.parent / '.root.img.partial').exists())
        self.assertTrue((self.parent / '.root-copy-in-progress').exists())
        self.assertIn('[step 4/5] Verifying written root.img', output.getvalue())
        self.assertIn('Failed or interrupted;', output.getvalue())
        self.assertNotIn('[step 5/5]', output.getvalue())

    def test_flush_failure_retains_transaction_and_stops_before_verification(self):
        actual_copy = api.copy_image
        def fail_flush(*args, **kwargs):
            actual_copy(*args, **kwargs)
            # The transaction marker was already durably written before copying.
            failure.start()
        failure = patch.object(api.os, 'fsync', side_effect=OSError('fixture flush failed'))
        self.addCleanup(failure.stop)
        output = io.StringIO()
        with patch.object(api, 'copy_image', side_effect=fail_flush), redirect_stdout(output):
            with self.assertRaisesRegex(OSError, 'fixture flush failed'):
                self.provision()
        self.assertTrue((self.parent / '.root.img.partial').exists())
        self.assertFalse((self.parent / 'root.img').exists())
        self.assertIn('[step 3/5] Flushing root.img to storage', output.getvalue())
        self.assertNotIn('[step 4/5]', output.getvalue())

    def test_state_and_replacement_reject_corrupt_written_data(self):
        original_copy = api.copy_image
        def corrupt(*args, **kwargs):
            original_copy(*args, **kwargs)
            args[1].seek(0)
            args[1].write(b'corrupt fixture copy')
        for kind in ('state', 'replacement'):
            with self.subTest(kind=kind):
                parent = self.parent / kind
                parent.mkdir()
                original = parent / 'root.img'
                original.write_bytes(b'original fixture root')
                output = io.StringIO()
                with redirect_stdout(output), patch.object(api, 'copy_image', side_effect=corrupt):
                    with self.assertRaisesRegex(api.SafetyError, 'verification failed'):
                        if kind == 'state':
                            STATE['create'](api, self.master, parent, self.manifest, 'fixture-PC',
                                            free_bytes=2 * 1024**3)
                        else:
                            REPLACE['copy_replace'](api, self.master, original, {}, self.manifest,
                                                    free_bytes=2 * 1024**3)
                self.assertEqual(original.read_bytes(), b'original fixture root')
                self.assertFalse((parent / 'state.img').exists())
                self.assertFalse((parent / 'state-image.json').exists())
                self.assertFalse((parent / 'root-image.json').exists())
                self.assertTrue(any(path.name.startswith('.') and path.name.endswith(('.img', '.partial'))
                                    for path in parent.iterdir()))
                self.assertIn('Failed or interrupted;', output.getvalue())
                self.assertNotIn('[step 5/5]', output.getvalue())

    def test_replacement_reports_five_stages_and_keeps_previous_root(self):
        target = self.parent / 'root.img'
        target.write_bytes(b'previous fixture root')
        receipt = {'schema': 1, 'version': 'old-fixture', 'size': target.stat().st_size,
                   'original_sha256': api.digest(target)}
        (self.parent / 'root-image.json').write_text(json.dumps(receipt))
        output = io.StringIO()
        with redirect_stdout(output):
            REPLACE['copy_replace'](api, self.master, target, receipt, self.manifest,
                                    free_bytes=2 * 1024**3)
        self.assert_steps(output.getvalue(), 'Replacing root.img', [
            'Verifying replacement root.img source', 'Copying replacement root.img',
            'Flushing replacement root.img to storage', 'Verifying written replacement root.img',
            'Finalizing root.img replacement; retaining previous image'])
        self.assertEqual(next(self.parent.glob('root-before-update-*.img')).read_bytes(),
                         b'previous fixture root')
        self.assertEqual(api.digest(target), self.manifest['sha256'])


if __name__ == '__main__':
    unittest.main()
