"""Command-only MOK helpers must stop before import on missing/wrong input."""
import importlib.util
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('shared_mok', Path(__file__).resolve().parents[1] / 'scripts/stage-shared-mok-commands.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SharedMokTests(unittest.TestCase):
    def test_no_certificate_copy_and_invalid_inputs_never_import(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cert = root / 'signing-cert.der'
            cert.write_bytes(b'public fixture parsed by mocked openssl')
            output = root / 'commands'
            real_run = subprocess.run

            def run(command, **kwargs):
                if command[0] == 'openssl':
                    return subprocess.CompletedProcess(command, 0)
                return real_run(command, **kwargs)

            with patch.object(module.subprocess, 'run', side_effect=run):
                module.stage(cert, output)
            self.assertEqual({p.name for p in output.iterdir()}, {
                'mok-0-prepare', 'mok-1-import', 'mok-2-check', 'README-FEDORA.txt'})
            result = real_run(['bash', str(output / 'mok-1-import')], env={'PATH': '/usr/bin:/bin'}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Set MOK_CERT', result.stderr)
            cert.write_bytes(b'wrong certificate')
            result = real_run(['bash', str(output / 'mok-1-import')], env={'PATH': '/usr/bin:/bin', 'MOK_CERT': str(cert)}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('SHA-256 mismatch', result.stderr)

    def test_import_helper_checks_actual_enrolled_fingerprint_not_test_key_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cert = root / 'signing-cert.der'
            cert.write_bytes(b'public fixture')
            output = root / 'commands'
            real_run = subprocess.run
            def run(command, **kwargs):
                if command[0] == 'openssl':
                    return subprocess.CompletedProcess(command, 0)
                return real_run(command, **kwargs)
            with patch.object(module.subprocess, 'run', side_effect=run):
                module.stage(cert, output)
            generated = (output / 'mok-1-import').read_text()
            start = generated.index('# Do not treat test-key')
            end = generated.index("printf 'Confirm", start)
            check = root / 'check.sh'
            check.write_text('set -euo pipefail\n' + generated[start:end] + "echo NEED_IMPORT\n")
            mock = root / 'bin'
            mock.mkdir()
            sudo = mock / 'sudo'
            sudo.write_text('#!/bin/sh\n[ "$1" = mokutil ] && [ "$2" = --list-enrolled ] || exit 91\nprintf "%s\\n" "$MOCK_LIST"\n')
            sudo.chmod(0o755)
            own = hashlib.sha1(cert.read_bytes()).hexdigest()
            own_text = 'SHA1 Fingerprint: ' + ':'.join(own[i:i+2] for i in range(0, 40, 2))
            other_text = 'SHA1 Fingerprint: ' + ':'.join(['22'] * 20)
            cases = [(own_text, 0, 'fingerprint is present'), (other_text, 0, 'NEED_IMPORT'),
                     ('', 0, 'NEED_IMPORT'), ('unrecognized response', 1, 'STOP')]
            for listing, code, expected in cases:
                with self.subTest(listing=listing):
                    result = real_run(['bash', str(check)], env={**os.environ, 'PATH': str(mock) + ':/usr/bin:/bin', 'MOCK_LIST': listing}, capture_output=True, text=True)
                    self.assertEqual(result.returncode, code)
                    self.assertIn(expected, result.stdout + result.stderr)
