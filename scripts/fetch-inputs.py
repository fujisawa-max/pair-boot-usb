#!/usr/bin/env python3
"""Fetch existing hash-locked inputs into .cache; never extract or install."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    'debian-build': ('debian-build-inputs.json', '.cache/debian/apt/archives'),
    'debian-runtime': ('debian-runtime-inputs.json', '.cache/debian/apt/archives'),
    'build-tools': ('debian-build-tools-inputs.json', '.cache/debian/apt/archives'),
    'support': ('debian-support-inputs.json', '.cache/debian/apt/archives'),
    'nvidia': ('nvidia-driver-inputs.json', '.cache/nvidia/apt/archives'),
    'network': ('candidate-network-inputs.json', '.cache/candidate-network'),
    'pair': ('pair-headless-input.json', None),
    'llamacpp': ('pair-llamacpp-inputs.json', None),
    'firmware': ('firmware-inputs.json', None),
    'fedora': ('fedora-live-input.json', None),
    'fedora-tools': ('fedora-tools-inputs.json', None),
    'public-keys': ('public-keys.json', None),
}
PREFIXES = (
    'https://deb.debian.org/debian/pool/',
    'https://deb.debian.org/debian-security/pool/',
    'https://security.debian.org/debian-security/pool/',
    'https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/',
    'https://github.com/NVIDIA/Personal-AI-Router/releases/download/',
    'https://github.com/ggml-org/llama.cpp/releases/download/',
    'https://download.nvidia.com/XFree86/NVIDIA-kernel-module-source/',
    'https://archive.cachyos.org/archive/cachyos-v3/',
    'https://dl.fedoraproject.org/pub/fedora/linux/releases/44/Workstation/x86_64/iso/',
    'https://files.pythonhosted.org/packages/',
    'https://ftp-master.debian.org/keys/',
    'https://fedoraproject.org/fedora.gpg',
)


def cache_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or path.parts[:1] != ('.cache',):
        raise ValueError(f'Input destination must be below .cache/: {relative}')
    target = root / path
    for item in (target, *target.parents):
        if item == root:
            break
        if item.is_symlink():
            raise ValueError(f'Refusing symlink cache path: {item}')
    if target.exists() and not target.is_file():
        raise ValueError(f'Expected regular cache file: {target}')
    return target


def input_plan(groups, root=ROOT):
    records = {}
    for group in groups:
        lock_name, directory = GROUPS[group]
        lock = json.loads((root / 'inputs' / lock_name).read_text())
        entries = lock['packages'] if directory else lock.get('archives', [lock])
        if not entries:
            raise ValueError(f'Empty input lock: {lock_name}')
        for entry in entries:
            if directory:
                name = entry['filename']
                if Path(name).name != name or not name.endswith('.deb'):
                    raise ValueError(f'Unsafe package filename: {name}')
                relative = f'{directory}/{name}'
            else:
                relative = entry.get('archive_file', entry.get('file'))
            cache_path(root, relative)
            url, sha = entry['url'], entry['sha256']
            if not url.startswith(PREFIXES) or not re.fullmatch('[0-9a-f]{64}', sha):
                raise ValueError(f'Invalid URL or SHA-256 in {lock_name}')
            size = entry.get('size')
            if size is not None and (type(size) is not int or size <= 0):
                raise ValueError(f'Invalid size in {lock_name}')
            record = {'file': relative, 'url': url, 'sha256': sha}
            if size is not None:
                record['size'] = size
            if relative in records:
                previous = records[relative]
                if any(previous[key] != record[key] for key in ('url', 'sha256')):
                    raise ValueError(f'Conflicting locks for {relative}')
                if 'size' in previous and size is not None and previous['size'] != size:
                    raise ValueError(f'Conflicting sizes for {relative}')
                previous.update(record)
            else:
                records[relative] = record
    return list(records.values())


def verify(path, entry):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Missing regular input: {path}')
    if 'size' in entry and path.stat().st_size != entry['size']:
        raise ValueError(f'Size mismatch: {path}')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != entry['sha256']:
        raise ValueError(f'SHA-256 mismatch: {path}')


class HTTPSRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith('https://'):
            raise ValueError('Refusing non-HTTPS redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(entry, root=ROOT, offline=False, opener=None):
    path = cache_path(root, entry['file'])
    if path.exists():
        verify(path, entry)
        return 'cached'
    if offline:
        raise ValueError(f'Missing offline input: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    if opener is None:
        opener = urllib.request.build_opener(HTTPSRedirects()).open
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.input-', delete=False) as target:
            temporary = Path(target.name)
            request = urllib.request.Request(entry['url'], headers={'User-Agent': 'pair-boot-usb-inputs/1'})
            with opener(request, timeout=60) as source:
                if not source.url.startswith('https://'):
                    raise ValueError('Refusing non-HTTPS response')
                total = 0
                while block := source.read(1024 * 1024):
                    total += len(block)
                    if 'size' in entry and total > entry['size']:
                        raise ValueError('Download exceeds locked size')
                    target.write(block)
        verify(temporary, entry)
        # A hard link publishes the verified file atomically without overwriting
        # another process's cache entry. Both files are on the same filesystem.
        cache_path(root, entry['file'])
        try:
            os.link(temporary, path)
        except FileExistsError:
            verify(path, entry)
        return 'downloaded'
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', action='append', choices=GROUPS,
                        help='repeat to select groups; default: all package/archive locks')
    parser.add_argument('--plan', action='store_true', help='print inputs without network or writes')
    parser.add_argument('--offline', action='store_true', help='verify existing cache only')
    args = parser.parse_args()
    records = input_plan(args.group or list(GROUPS))
    if args.plan:
        print(json.dumps({'inputs': records, 'count': len(records)}, indent=2))
        return
    for entry in records:
        status = fetch(entry, offline=args.offline)
        print(f"{status}: {entry['file']}", flush=True)
    print(f'Verified {len(records)} inputs against local locks; no upstream signature verification performed.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f'Input acquisition failed: {error}')
