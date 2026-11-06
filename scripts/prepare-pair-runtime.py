#!/usr/bin/env python3
"""Prepare verified Debian runtime data locally, with no package installation."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import runpy

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/pair-runtime-overlay-v1')
    args = parser.parse_args()
    output = runpy.run_path(str(ROOT / 'scripts/prepare-firmware.py'))['fresh_output'](args.output)
    fetch = runpy.run_path(str(ROOT / 'scripts/fetch-inputs.py'))
    lock = json.loads((ROOT / 'inputs/debian-support-inputs.json').read_text())
    entries = [e for e in lock['packages'] if e['role'] == 'pair-runtime']
    if {e['package'] for e in entries} != {'gzip', 'ca-certificates'} or len(entries) != 2:
        raise ValueError('Expected exactly the pinned gzip and public CA packages')
    records = []
    for entry in entries:
        relative = '.cache/debian/apt/archives/' + entry['filename']
        package = fetch['cache_path'](ROOT, relative)
        fetch['verify'](package, entry)
        records.append({**entry, 'file': relative})
    output.mkdir(parents=True)
    for record in records:
        subprocess.run(['dpkg-deb', '--extract', str(ROOT / record['file']), str(output)], check=True)
    certs = sorted((output / 'usr/share/ca-certificates/mozilla').glob('*.crt'))
    if len(certs) < 100:
        raise ValueError('Incomplete public CA bundle')
    bundle = output / 'etc/ssl/certs/ca-certificates.crt'
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_bytes(b''.join(p.read_bytes().rstrip() + b'\n' for p in certs))
    subprocess.run(['openssl', 'crl2pkcs7', '-nocrl', '-certfile', str(bundle), '-outform', 'DER'], check=True, stdout=subprocess.DEVNULL)
    pam = output / 'etc/pam.d'
    pam.mkdir(parents=True, exist_ok=True)
    # Complete the Debian fallback includes without permitting empty passwords.
    for name, text in {
        'common-auth': 'auth required pam_unix.so\n',
        'common-account': 'account required pam_unix.so\n',
        'common-password': 'password required pam_unix.so yescrypt\n',
        'common-session': 'session required pam_unix.so\n',
        'common-session-noninteractive': 'session required pam_unix.so\n',
    }.items():
        (pam / name).write_text('# Minimal appliance local-account policy\n' + text)
    (output / 'pair-runtime-inputs.json').write_text(json.dumps({'schema': 1, 'packages': records, 'ca_count': len(certs), 'ca_sha256': hashlib.sha256(bundle.read_bytes()).hexdigest(), 'action': 'data extraction; no maintainer scripts'}, indent=2) + '\n')
    print(output)


if __name__ == '__main__':
    main()
