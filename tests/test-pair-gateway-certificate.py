"""Exercise the operator CLI only with short-lived, synthetic OpenSSL fixtures."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/issue-pair-gateway-certificate.py'


@unittest.skipUnless(shutil.which('openssl'), 'OpenSSL is needed for the synthetic certificate fixture')
class GatewayCertificateTests(unittest.TestCase):
    def test_issue_checks_csr_ca_hostname_and_safe_output(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            ca, key, csr, other = [directory / name for name in
                                   ('ca.pem', 'ca.key', 'node.csr', 'other.csr')]
            self.run_ssl('req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
                         '-noenc', '-days', '5', '-subj', '/CN=Synthetic Test CA',
                         '-addext', 'basicConstraints=critical,CA:TRUE',
                         '-addext', 'keyUsage=critical,keyCertSign',
                         '-keyout', key, '-out', ca)
            key.chmod(0o600)
            for request in (csr, other):
                self.run_ssl('req', '-new', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
                             '-noenc', '-subj', '/CN=unapproved.example',
                             '-addext', 'subjectAltName=DNS:unapproved.example',
                             '-addext', 'basicConstraints=critical,CA:TRUE',
                             '-keyout', directory / (request.stem + '.key'), '-out', request)
            output = directory / 'result'
            common = ['--ca-cert', ca, '--dns-name', 'pair-lab.example']
            issuing = ['issue', *common, '--days', '1', '--ca-key', key, '--output', output]
            result = self.cli(*issuing, '--csr', csr)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual({file.name for file in output.iterdir()}, {'fullchain.pem', 'ca.pem'})
            self.assertEqual((output / 'ca.pem').read_bytes(), ca.read_bytes())
            leaf = output / 'fullchain.pem'
            original = leaf.read_bytes()
            self.assertNotEqual(self.cli(*issuing, '--csr', csr).returncode, 0)
            self.assertEqual(leaf.read_bytes(), original)
            self.assertEqual(self.cli('verify', *common, '--csr', csr, '--certificate', leaf).returncode, 0)
            self.assertNotEqual(self.cli('verify', *common, '--csr', other, '--certificate', leaf).returncode, 0)
            self.assertNotEqual(self.cli('verify', '--ca-cert', ca, '--dns-name', 'wrong.example',
                                         '--csr', csr, '--certificate', leaf).returncode, 0)
            link = directory / 'linked.csr'
            link.symlink_to(csr)
            self.assertNotEqual(self.cli('verify', *common, '--csr', link, '--certificate', leaf).returncode, 0)
            extra = directory / 'extra.csr'
            extra.write_bytes(csr.read_bytes() * 2)
            self.assertNotEqual(self.cli('verify', *common, '--csr', extra, '--certificate', leaf).returncode, 0)
            self.assertIn(b'DNS:pair-lab.example', self.run_ssl(
                'x509', '-in', leaf, '-noout', '-ext', 'subjectAltName'))
            self.assertIn(b'CA:FALSE', self.run_ssl(
                'x509', '-in', leaf, '-noout', '-ext', 'basicConstraints'))

            node = directory / 'node'
            request = ['request', '--dns-name', 'pair-lab.example', '--output', node]
            result = self.cli(*request)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(node.stat().st_mode & 0o777, 0o700)
            self.assertEqual({file.name for file in node.iterdir()}, {'key.pem', 'node.csr.pem'})
            node_key = node / 'key.pem'
            node_csr = node / 'node.csr.pem'
            self.assertEqual(node_key.stat().st_mode & 0o777, 0o600)
            public_key = self.run_ssl('pkey', '-in', node_key, '-pubout')
            self.assertEqual(public_key, self.run_ssl('req', '-in', node_csr, '-noout', '-pubkey'))
            self.assertNotEqual(self.cli(*request).returncode, 0)
            self.assertEqual(public_key, self.run_ssl('pkey', '-in', node_key, '-pubout'))
            issued = directory / 'issued-node'
            self.assertEqual(self.cli('issue', *common, '--days', '1', '--ca-key', key,
                                      '--output', issued, '--csr', node_csr).returncode, 0)
            self.assertEqual(public_key, self.run_ssl(
                'x509', '-in', issued / 'fullchain.pem', '-noout', '-pubkey'))
            verify = ['verify', *common, '--csr', node_csr,
                      '--certificate', issued / 'fullchain.pem']
            result = self.cli(*verify, '--node-key', node_key)
            self.assertEqual(result.returncode, 0, result.stderr)
            other_key = directory / 'other.key'
            other_key.chmod(0o600)
            result = self.cli(*verify, '--node-key', other_key)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('does not match the local node key', result.stderr)
            key_link = directory / 'linked.key'
            key_link.symlink_to(node_key)
            self.assertNotEqual(self.cli(*verify, '--node-key', key_link).returncode, 0)
            node_key.chmod(0o644)
            self.assertNotEqual(self.cli(*verify, '--node-key', node_key).returncode, 0)

    @staticmethod
    def run_ssl(*args):
        return subprocess.run(['openssl', *map(str, args)], capture_output=True, check=True, timeout=15).stdout

    @staticmethod
    def cli(*args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                              capture_output=True, text=True, timeout=15)
