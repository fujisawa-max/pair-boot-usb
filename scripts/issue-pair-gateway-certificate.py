#!/usr/bin/env python3
"""Prepare a node CSR, or issue and verify its public HTTPS certificate."""
import argparse
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DNS = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+'
                 r'[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?', re.ASCII)
PEM_LIMIT = 64 * 1024


def ordinary(raw):
    path = Path(raw).absolute()
    if '..' in path.parts or any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('Use normalized paths without symlinks')
    return path


def public_pem(raw, label):
    path = ordinary(raw)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Expected an ordinary public PEM file')
        data = stream.read(PEM_LIMIT + 1)
    pattern = rb'-----BEGIN ' + label + rb'-----\s+[A-Za-z0-9+/=\r\n]+-----END ' + label + rb'-----'
    if len(data) > PEM_LIMIT or not re.fullmatch(pattern, data.strip()):
        raise ValueError('Expected exactly one bounded public PEM object')
    return data


def openssl(*args, interactive=False):
    argv = ['openssl', *map(str, args)]
    if interactive:
        # Let OpenSSL prompt for the CA key passphrase on the owner's terminal.
        subprocess.run(argv, check=True)
        return None
    return subprocess.run(argv, check=True, capture_output=True, timeout=15).stdout


def check_certificate(csr, ca, certificate, hostname, node_key=None):
    openssl('verify', '-CAfile', ca, '-no-CApath', '-no-CAstore',
            '-check_ss_sig', '-purpose', 'sslserver', '-verify_hostname', hostname, certificate)
    san = openssl('x509', '-in', certificate, '-noout', '-ext', 'subjectAltName')
    names = san.partition(b'\n')[2].strip()
    if names != b'DNS:' + hostname.encode('ascii'):
        raise ValueError('TLS certificate must contain only the approved DNS SAN')
    extensions = openssl('x509', '-in', certificate, '-noout', '-ext', 'basicConstraints')
    if b'CA:FALSE' not in extensions or b'CA:TRUE' in extensions:
        raise ValueError('Expected a leaf TLS certificate')
    requested = openssl('req', '-in', csr, '-noout', '-pubkey')
    issued = openssl('x509', '-in', certificate, '-noout', '-pubkey')
    issuer = openssl('x509', '-in', ca, '-noout', '-pubkey')
    if not requested or requested != issued or requested == issuer:
        raise ValueError('Certificate does not match a distinct node CSR key')
    if node_key is not None and node_key != issued:
        raise ValueError('Certificate does not match the local node key')


def local_node_public_key(raw):
    key = ordinary(raw)
    if key.is_relative_to(ROOT) or key.is_relative_to('/dev'):
        raise ValueError('Use an owner-only node key on controlled storage outside the repository')
    fd = os.open(key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('Use a regular node key file accessible only to its owner')
        result = subprocess.run(
            ['openssl', 'pkey', '-passin', 'pass:', '-pubout'], stdin=stream,
            capture_output=True, check=True, timeout=15)
        return result.stdout


def new_destination(raw):
    destination = ordinary(raw)
    if (os.path.lexists(destination) or not destination.parent.is_dir()
            or destination.is_relative_to(ROOT) or destination.is_relative_to('/dev')):
        raise ValueError('Use a fresh output directory outside the repository with an existing parent')
    return destination


def prepare_request(args):
    destination = new_destination(args.output)
    with tempfile.TemporaryDirectory(prefix='.pair-gateway-request-', dir=destination.parent) as temp:
        work = Path(temp)
        key, csr = work / 'key.pem', work / 'node.csr.pem'
        openssl('req', '-new', '-newkey', 'rsa:2048', '-noenc', '-sha256',
                '-subj', f'/CN={args.dns_name}', '-addext', f'subjectAltName=DNS:{args.dns_name}',
                '-keyout', key, '-out', csr)
        key.chmod(0o600)
        csr.chmod(0o600)
        public_pem(csr, b'CERTIFICATE REQUEST')
        openssl('req', '-in', csr, '-noout', '-verify')
        destination.mkdir(mode=0o700)
        key.rename(destination / key.name)
        csr.rename(destination / csr.name)


def issue(args, work, csr, ca):
    key = ordinary(args.ca_key)
    if (key.is_relative_to(ROOT) or key.is_relative_to('/dev') or not key.is_file()
            or key.stat().st_mode & 0o077):
        raise ValueError('Use an owner-only CA key on controlled storage outside the repository')
    destination = new_destination(args.output)
    if not 1 <= args.days <= 365:
        raise ValueError('Certificate validity must be between 1 and 365 days')
    openssl('x509', '-in', ca, '-noout', '-checkend', args.days * 86400 + 60)
    config = work / 'tls.cnf'
    config.write_text(
        'basicConstraints=critical,CA:FALSE\n'
        'keyUsage=critical,digitalSignature\n'
        'extendedKeyUsage=serverAuth\n'
        'subjectKeyIdentifier=hash\n'
        'authorityKeyIdentifier=keyid,issuer\n'
        f'subjectAltName=DNS:{args.dns_name}\n', encoding='ascii')
    certificate = work / 'fullchain.pem'
    openssl('x509', '-req', '-in', csr, '-CA', ca, '-CAkey', key,
            '-subj', f'/CN={args.dns_name}', '-copy_extensions', 'none',
            '-sha256', '-days', args.days, '-set_serial', f'0x01{secrets.token_hex(15)}',
            '-extfile', config, '-out', certificate, interactive=True)
    check_certificate(csr, ca, certificate, args.dns_name)
    leaf = public_pem(certificate, b'CERTIFICATE')
    destination.mkdir(mode=0o700)
    for name, data in (('fullchain.pem', leaf), ('ca.pem', ca.read_bytes())):
        fd = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    request = commands.add_parser('request', help='generate a node key and public CSR')
    issuing = commands.add_parser('issue', help='sign an authenticated node CSR with the staff CA')
    checking = commands.add_parser('verify', help='check the returned certificate with the original CSR and trusted CA')
    for command in (issuing, checking):
        command.add_argument('--dns-name', required=True)
        command.add_argument('--csr', type=Path, required=True)
        command.add_argument('--ca-cert', type=Path, required=True)
    issuing.add_argument('--ca-key', type=Path, required=True)
    request.add_argument('--dns-name', required=True)
    request.add_argument('--output', type=Path, required=True)
    issuing.add_argument('--output', type=Path, required=True)
    issuing.add_argument('--days', type=int, default=30)
    checking.add_argument('--certificate', type=Path, required=True)
    checking.add_argument('--node-key', type=Path, help='also check the local node private key')
    args = parser.parse_args()
    if len(args.dns_name) > 253 or not DNS.fullmatch(args.dns_name):
        raise ValueError('Specify the approved lowercase gateway DNS name')
    if args.action == 'request':
        prepare_request(args)
        print('Generated a private node key and public node.csr.pem. Transfer only the CSR.')
        return
    request = public_pem(args.csr, b'CERTIFICATE REQUEST')
    authority = public_pem(args.ca_cert, b'CERTIFICATE')
    with tempfile.TemporaryDirectory(prefix='pair-gateway-public-') as temp:
        work = Path(temp)
        csr, ca = work / 'node.csr.pem', work / 'ca.pem'
        csr.write_bytes(request)
        ca.write_bytes(authority)
        if args.action == 'issue':
            issue(args, work, csr, ca)
        else:
            certificate = work / 'fullchain.pem'
            certificate.write_bytes(public_pem(args.certificate, b'CERTIFICATE'))
            node_key = local_node_public_key(args.node_key) if args.node_key is not None else None
            check_certificate(csr, ca, certificate, args.dns_name, node_key)
            if node_key is not None:
                print('Verified the local node key matches the server certificate.')
    print('Verified node certificate, approved DNS SAN, and CSR public key.')


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError:
        sys.exit('OpenSSL rejected the request, CA, or TLS certificate.')
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        sys.exit(f'Gateway certificate operation stopped: {error}')
