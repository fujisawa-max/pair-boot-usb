#!/usr/bin/env python3
"""Fetch the pinned public gpu-unlock commit without installing it."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    environment = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
                       GIT_TERMINAL_PROMPT='0')
    return subprocess.check_output(['git', *map(str, args)], env=environment, text=True).strip()


def verify(directory, commit):
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Expected an ordinary upstream directory')
    if git('-C', directory, 'rev-parse', 'HEAD') != commit:
        raise ValueError('Upstream checkout has the wrong commit')
    if git('-C', directory, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('Upstream checkout contains local changes; use a fresh checkout')
    git('-C', directory, 'fsck', '--strict', '--no-reflogs')
    if not (directory / 'kernel/gpu_unlock/Kbuild').is_file():
        raise ValueError('Upstream checkout lacks the module source')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    lock = json.loads((ROOT / 'inputs/gpu-unlock-source.json').read_text())
    if lock != {'schema': 1, 'url': 'https://github.com/Zagitta/gpu-unlock.git',
                'commit': 'af28cbbfd3d922a659227ecfb7369adfd924cf03', 'directory': 'vendor/gpu-unlock'}:
        raise ValueError('Unexpected gpu-unlock source lock')
    destination = ROOT / lock['directory']
    if (ROOT / 'vendor').is_symlink() or destination.is_symlink():
        raise ValueError('Refusing symlink source path')
    if destination.exists():
        verify(destination, lock['commit'])
    else:
        if args.offline:
            raise ValueError('Pinned upstream checkout is missing')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.gpu-unlock-', dir=destination.parent) as temporary:
            checkout = Path(temporary) / 'source'
            git('init', checkout)
            git('-C', checkout, '-c', 'http.followRedirects=false', 'fetch', '--depth=1', lock['url'], lock['commit'])
            git('-C', checkout, 'checkout', '--detach', 'FETCH_HEAD')
            verify(checkout, lock['commit'])
            if destination.exists():
                raise ValueError('Upstream destination appeared during fetch')
            checkout.rename(destination)
    print(f"Verified gpu-unlock commit {lock['commit']}; no installation performed.")


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f'Upstream preparation failed: {error}')
