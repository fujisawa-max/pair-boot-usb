#!/usr/bin/env python3
"""Owner-run creation of an encrypted module-signing key outside the repository."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def key_directory(raw):
    directory = Path(raw).absolute()
    if '..' in directory.parts:
        raise ValueError('Use a normalized absolute storage path')
    for item in (directory, *directory.parents):
        if item.is_symlink():
            raise ValueError('Key storage path must not contain symlinks')
    if directory.is_relative_to(ROOT) or directory.is_relative_to('/dev'):
        raise ValueError('Choose controlled storage outside the repository and device paths')
    if directory.exists():
        raise ValueError('Key directory already exists; refusing to overwrite it')
    if not directory.parent.is_dir():
        raise ValueError('The controlled parent storage directory must already exist')
    return directory


def create(raw, runner=subprocess.run):
    directory = key_directory(raw)
    previous = os.umask(0o077)
    try:
        directory.mkdir(mode=0o700)
        # OpenSSL prompts on the owner's terminal. No passphrase argument,
        # environment variable, plaintext key option, or private key output.
        runner(['openssl', 'req', '-new', '-x509', '-newkey', 'rsa:3072',
                '-sha256', '-days', '3650', '-keyout', str(directory / 'module-signing.key.pem'),
                '-outform', 'DER', '-out', str(directory / 'module-signing.cert.der'),
                '-subj', '/CN=PAIR Boot USB Module Signing/'], check=True)
    finally:
        os.umask(previous)
    print('Encrypted key and public DER certificate created in the selected storage directory.')
    print('Keep the key outside the repository, root image, and USB.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True,
                        help='new directory in controlled storage outside repository and USB')
    args = parser.parse_args()
    create(args.directory)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f'Key creation failed; preserve and inspect any partial output before retrying: {error}')
