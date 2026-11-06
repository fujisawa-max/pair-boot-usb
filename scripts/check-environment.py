#!/usr/bin/env python3
"""Check fresh-clone host prerequisites without downloads or host changes."""
import argparse
import json
from pathlib import Path
import platform
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
MIN_PYTHON = (3, 11)
MIN_FREE_GIB = 64
# Ubuntu/Debian package names, shared by the checker and installer.
TOOL_PACKAGES = {
    'git': 'git', 'dpkg-deb': 'dpkg', 'apt-get': 'apt', 'apt-cache': 'apt',
    'gpg': 'gnupg', 'gpgv': 'gpgv', 'openssl': 'openssl',
    'bwrap': 'bubblewrap', 'fakeroot': 'fakeroot',
    'mke2fs': 'e2fsprogs', 'e2fsck': 'e2fsprogs', 'debugfs': 'e2fsprogs',
    'depmod': 'kmod', 'modinfo': 'kmod', 'zstd': 'zstd',
    'sh': 'dash', 'bash': 'bash', 'tar': 'tar', 'xz': 'xz-utils',
    'sha256sum': 'coreutils', 'cp': 'coreutils', 'mktemp': 'coreutils',
}
APT_PACKAGES = tuple(sorted(set(TOOL_PACKAGES.values()) | {'ca-certificates', 'python3'}))


def inspect_environment(root, *, python_version=None, system=None, machine=None):
    version = sys.version_info if python_version is None else python_version
    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    report = {'python': '.'.join(map(str, version[:3])), 'system': system,
              'machine': machine, 'missing': [], 'missing_packages': []}
    # Give old Python a clear error before inspecting build tools or storage.
    if tuple(version[:2]) < MIN_PYTHON:
        report['missing'].append('Python >=3.11 is required; running ' + report['python']
                                 + '. Activate the environment from environment.yml; see docs/build.md.')
    else:
        if system != 'Linux' or machine.lower() not in ('x86_64', 'amd64'):
            report['missing'].append('An x86_64 Linux or WSL2 Linux build host is required')
        report['tools'] = {name: shutil.which(name) for name in TOOL_PACKAGES}
        absent = [name for name, path in report['tools'].items() if not path]
        if absent:
            packages = sorted({TOOL_PACKAGES[name] for name in absent})
            report['missing_packages'] = packages
            report['missing'].append('Missing build prerequisites: ' + ', '.join(absent)
                                     + '. Ubuntu/Debian packages: ' + ', '.join(packages)
                                     + '. Run sh scripts/install-build-dependencies.sh; see docs/build.md.')
        report['free_gib'] = shutil.disk_usage(root).free / 1024**3
        if report['free_gib'] < MIN_FREE_GIB:
            report['missing'].append('At least 64 GiB of free build storage is required')
    report['build_prerequisites_present'] = not report['missing']
    report['scope'] = ('Checks Python, platform, command availability and free storage only. '
                       'Isolation, downloads, signatures and hardware require separate validation.')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--json', action='store_true', help='print a structured report')
    mode.add_argument('--apt-packages', action='store_true', help='print installer package names only')
    args = parser.parse_args()
    if args.apt_packages:
        print(' '.join(APT_PACKAGES))
        return 0
    report = inspect_environment(ROOT)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print('Python ' + report['python'] + '; ' + report['system'] + ' ' + report['machine'])
        if 'free_gib' in report:
            print(f"Free build storage: {report['free_gib']:.1f} GiB (minimum {MIN_FREE_GIB} GiB)")
        for message in report['missing']:
            print('ERROR: ' + message)
        if report['build_prerequisites_present']:
            print('Host prerequisites present. Next: run the source tests, then build-usb.py prepare.')
        print(report['scope'])
    return 0 if report['build_prerequisites_present'] else 1


if __name__ == '__main__':
    sys.exit(main())
