#!/usr/bin/env python3
"""Extract fixed Debian signed shim/GRUB inputs into a fresh build directory."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FILES = ('usr/lib/shim/shimx64.efi.signed', 'usr/lib/shim/mmx64.efi.signed',
         'usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed')


def check_pe_certificate(data):
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('EFI input is not a PE image')
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    optional = pe + 24
    if data[pe:pe+4] != b'PE\0\0' or len(data) < optional + 152:
        raise ValueError('Invalid PE header')
    if struct.unpack_from('<H', data, optional)[0] != 0x20b:
        raise ValueError('Expected PE32+ x86_64 EFI input')
    offset, size = struct.unpack_from('<II', data, optional + 112 + 8 * 4)
    if size < 8 or offset + size > len(data) or offset % 8:
        raise ValueError('EFI input lacks a valid certificate table')
    length, revision, kind = struct.unpack_from('<IHH', data, offset)
    if not 8 < length <= size or revision != 0x200 or kind != 2:
        raise ValueError('Expected an Authenticode certificate record')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/efi-inputs')
    args = parser.parse_args()
    output = runpy.run_path(str(ROOT / 'scripts/prepare-firmware.py'))['fresh_output'](args.output)
    fetch = runpy.run_path(str(ROOT / 'scripts/fetch-inputs.py'))
    lock = json.loads((ROOT / 'inputs/debian-support-inputs.json').read_text())
    entries = [e for e in lock['packages'] if e['role'] == 'signed-efi']
    inputs = []
    for entry in entries:
        path = ROOT / '.cache/debian/apt/archives' / entry['filename']
        fetch['cache_path'](ROOT, str(path.relative_to(ROOT)))
        fetch['verify'](path, entry)
        inputs.append(path)
    output.mkdir(parents=True)
    for path in inputs:
        subprocess.run(['dpkg-deb', '--extract', str(path), str(output)], check=True)
    hashes = {}
    for name in FILES:
        path = output / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Missing regular signed EFI input: {name}')
        data = path.read_bytes()
        check_pe_certificate(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    (output / 'efi-verification.json').write_text(json.dumps({'schema': 1, 'packages': entries,
        'files': hashes, 'certificate_table_present': True, 'native_trust_tested': False}, indent=2) + '\n')
    print(f'Extracted three signed EFI inputs into {output}; no bootloader installed on host.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, struct.error, subprocess.CalledProcessError) as error:
        sys.exit(f'Signed EFI preparation failed: {error}')
