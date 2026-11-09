#!/usr/bin/env python3
"""Optional pinned PowerShell fixture runner; no host installation or USB staging."""
import argparse
import json
from pathlib import Path, PurePosixPath
import runpy
import shutil
import tarfile
import sys
import platform

ROOT = Path(__file__).resolve().parents[1]


def extract(archive, output):
    for parent in (output, *output.parents):
        if parent.is_symlink():
            raise ValueError('Symlink output paths are forbidden')
    if output.exists() or output.is_symlink():
        raise ValueError('Choose a fresh PowerShell test output directory')
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        names = set()
        if len(members) > 10000 or sum(member.size for member in members) > 512 * 1024**2:
            raise ValueError('PowerShell archive exceeds fixture tool limits')
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '\\' in member.name or ':' in member.name or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe archive member; links and special files are forbidden')
            name = str(path).casefold()
            if name in names:
                raise ValueError('Duplicate archive path')
            names.add(name)
        output.mkdir(parents=True)
        for member in members:
            path = output / member.name
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as stream, path.open('xb') as target:
                    shutil.copyfileobj(stream, target)
                path.chmod(member.mode & 0o777)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux' or platform.machine().lower() not in ('x86_64', 'amd64'):
        raise ValueError('This optional fixture runner is for x86_64 Linux/WSL only')
    lock = json.loads((ROOT / 'inputs/powershell-test-input.json').read_text())
    if lock['version'] != '7.5.4':
        raise ValueError('Unsupported fixture runner version')
    entry, = lock['archives']
    if entry['url'] != 'https://github.com/PowerShell/PowerShell/releases/download/v7.5.4/powershell-7.5.4-linux-x64.tar.gz':
        raise ValueError('Unexpected PowerShell release URL')
    output = ROOT / 'build/powershell-7.5.4'
    if output.exists() or output.is_symlink():
        raise ValueError('PowerShell fixture output exists; preserve and inspect it')
    fetch = runpy.run_path(str(ROOT / 'scripts/fetch-inputs.py'))
    fetch['fetch'](entry, ROOT, offline=args.offline)
    extract(ROOT / entry['file'], output)
    (output / 'pwsh').chmod(0o755)
    print('Optional fixture runner prepared in build/powershell-7.5.4; host and USB unchanged.')


if __name__ == '__main__':
    main()
