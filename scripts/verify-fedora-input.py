#!/usr/bin/env python3
"""Verify Fedora's signed CHECKSUM with the pinned official key and ISO hash."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def checksum_matches(data, filename, sha):
    matches = re.findall(r'^SHA256 \(([^\r\n]+)\) = ([0-9a-f]{64})$', data.decode(), re.M)
    selected = [digest for name, digest in matches if name == filename]
    if selected != [sha]:
        raise ValueError('Signed Fedora CHECKSUM does not uniquely match the pinned ISO')


def verify(offline=False):
    public = runpy.run_path(str(ROOT / 'scripts/public-inputs.py'))
    lock = json.loads((ROOT / 'inputs/fedora-live-input.json').read_text())
    checksum = public['metadata_file'](lock['checksum_url'], lock['checksum_file'], offline=offline)
    plain = public['signed_cleartext'](checksum, lock['key_name'])
    entry = lock['archives'][0]
    iso = ROOT / entry['file']
    checksum_matches(plain, iso.name, entry['sha256'])
    public['FETCH']['cache_path'](ROOT, entry['file'])
    public['FETCH']['verify'](iso, entry)
    return {'schema': 1, 'iso_sha256': entry['sha256'],
            'checksum_sha256': hashlib.sha256(checksum.read_bytes()).hexdigest(),
            'official_key_fingerprint': public['public_key'](lock['key_name'])[1]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    result = verify(args.offline)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f'Fedora input verification failed: {error}')
