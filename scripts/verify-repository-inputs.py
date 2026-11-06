#!/usr/bin/env python3
"""Verify fixed package locks with authenticated, workspace-only APT indices."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / '.cache/repositories'
LOCKS = ('debian-build-inputs.json', 'debian-runtime-inputs.json', 'debian-build-tools-inputs.json',
         'nvidia-driver-inputs.json', 'candidate-network-inputs.json', 'debian-support-inputs.json')


def fields(text):
    records = []
    for stanza in text.strip().split('\n\n'):
        values = {}
        for line in stanza.splitlines():
            if line and not line[0].isspace() and ': ' in line:
                key, value = line.split(': ', 1)
                if key in values:
                    raise ValueError(f'Duplicate package metadata field: {key}')
                values[key] = value
        if values:
            records.append(values)
    return records


def package_matches(entry, candidate):
    if any(str(entry.get(key, '')) != candidate.get(field, '') for key, field in
           (('package', 'Package'), ('version', 'Version'), ('sha256', 'SHA256'))):
        return False
    if 'architecture' in entry and entry['architecture'] != candidate.get('Architecture'):
        return False
    if 'size' in entry and str(entry['size']) != candidate.get('Size'):
        return False
    # APT indices carry paths relative to each repository. Compare the exact
    # published path, allowing the two official Debian security hostnames.
    url = entry['url']
    prefixes = ('https://deb.debian.org/debian/', 'https://deb.debian.org/debian-security/',
                'https://security.debian.org/debian-security/',
                'https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/')
    for prefix in prefixes:
        if url.startswith(prefix):
            return url[len(prefix):] == candidate.get('Filename', '').removeprefix('./')
    return False


def configure():
    public = runpy.run_path(str(ROOT / 'scripts/public-inputs.py'))
    sources = []
    repositories = json.loads((ROOT / 'inputs/repositories.json').read_text())['repositories']
    # Do not permit symlink aliases for cache/configuration paths.
    for folder in (BASE, BASE / 'apt/lists/partial', BASE / 'apt/archives/partial', BASE / 'logs', BASE / 'empty.d'):
        for item in (folder, *folder.parents):
            if item == ROOT:
                break
            if item.is_symlink():
                raise ValueError('Refusing symlink repository cache')
        folder.mkdir(parents=True, exist_ok=True)
    for repo in repositories:
        key, _ = public['public_key'](repo['key'])
        sources.append(f"deb [arch=amd64 signed-by={key}] {repo['source']} {repo['suite']} {repo['components']}".rstrip())
    source_file = BASE / 'sources.list'
    if any(path.is_symlink() for path in (source_file, BASE / 'status', BASE / 'apt.conf')):
        raise ValueError('Refusing symlink repository configuration')
    source_file.write_text('\n'.join(sources) + '\n')
    status = BASE / 'status'
    status.touch(exist_ok=True)
    config = BASE / 'apt.conf'
    config.write_text('\n'.join([
        'Dir::Etc::main "/dev/null";', f'Dir::Etc::parts "{BASE / "empty.d"}";',
        f'Dir::Etc::sourceparts "{BASE / "empty.d"}";',
        'Dir::Etc::trusted "/dev/null";', f'Dir::Etc::trustedparts "{BASE / "empty.d"}";',
        'Dir::Etc::netrc "/dev/null";', f'Dir::Etc::netrcparts "{BASE / "empty.d"}";',
        f'Dir::Etc::sourcelist "{source_file}";', f'Dir::State "{BASE / "apt"}";',
        f'Dir::State::status "{status}";', f'Dir::Cache "{BASE / "apt"}";',
        f'Dir::Cache::archives "{BASE / "apt/archives"}";', f'Dir::Log "{BASE / "logs"}";',
        'APT::Architecture "amd64";', 'APT::Architectures { "amd64"; };',
        'Acquire::Languages "none";', 'Acquire::AllowInsecureRepositories "false";',
        'Acquire::Check-Valid-Until "true";', 'APT::Get::AllowUnauthenticated "false";',
        'APT::Update::Error-Mode "any";', 'Debug::NoLocking "true";',
    ]) + '\n')
    return dict(os.environ, APT_CONFIG=str(config))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata-only', action='store_true', help='prepare authenticated indices before maintaining locks')
    args = parser.parse_args()
    environment = configure()
    subprocess.run(['apt-get', 'update', '--error-on=any'], env=environment, check=True)
    if args.metadata_only:
        print('Authenticated workspace indices ready; no package locks changed.')
        return
    entries = []
    for name in LOCKS:
        path = ROOT / 'inputs' / name
        if path.exists():
            entries.extend(json.loads(path.read_text())['packages'])
    selectors = sorted({entry['package'] + '=' + entry['version'] for entry in entries})
    result = subprocess.run(['apt-cache', 'show', *selectors], env=environment,
                            check=True, capture_output=True, text=True)
    candidates = fields(result.stdout)
    missing = [entry for entry in entries if not any(package_matches(entry, item) for item in candidates)]
    if missing:
        raise ValueError('Locked packages absent from authenticated metadata: ' +
                         ', '.join(sorted({e['package'] + '=' + e['version'] for e in missing})))
    report = {'schema': 1, 'verified_lock_entries': len(entries),
              'host_apt_changed': False, 'package_installation_performed': False,
              'locks': {name: hashlib.sha256((ROOT / 'inputs' / name).read_bytes()).hexdigest()
                        for name in LOCKS if (ROOT / 'inputs' / name).exists()}}
    output = ROOT / 'build/repository-verification.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(f'Checked {len(entries)} lock entries against authenticated upstream package indices.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f'Repository verification failed: {error}')
