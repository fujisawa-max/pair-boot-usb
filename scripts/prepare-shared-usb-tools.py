#!/usr/bin/env python3
"""Stage shared tools and empty registry using explicitly supplied public inputs."""
import argparse
import json
from pathlib import Path
import runpy
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.9.0-shared'


def fresh_output(raw):
    path = raw.absolute()
    if '..' in path.parts or not path.is_relative_to(ROOT / 'build') or path == ROOT / 'build':
        raise ValueError('Fresh build/ output directories required')
    for item in (path, *path.parents):
        if item == ROOT:
            break
        if item.is_symlink():
            raise ValueError('Refusing symlink output path')
    if path.exists():
        raise ValueError('Fresh staging directories required')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/shared-usb-tools')
    parser.add_argument('--fedora-output', type=Path, default=ROOT / 'build/fedora-live-tools')
    parser.add_argument('--fedora-source', type=Path, required=True, help='freshly extracted Fedora tree below build/')
    parser.add_argument('--certificate', type=Path, required=True, help='public DER from the verified signing bundle')
    parser.add_argument('--media-version', choices=['0.7.0', '0.7.1', '0.7.2', '0.7.3', '0.7.4', '0.7.5', '0.7.6', '0.7.7', '0.7.8', '0.7.9', '0.8.0', '0.9.0'], default='0.9.0')
    parser.add_argument('--root-version', default=VERSION)
    args = parser.parse_args()
    output, fedora = fresh_output(args.output), fresh_output(args.fedora_output)
    source = args.fedora_source.resolve()
    cert = args.certificate
    if (not source.is_relative_to(ROOT / 'build') or args.fedora_source.is_symlink() or
            not (source / 'fedora-manifest.json').is_file() or
            not (source / 'boot/x86_64/loader/initrd').is_file()):
        parser.error('A generated Fedora source tree below build/ is required')
    if (source == fedora or source.is_relative_to(fedora) or fedora.is_relative_to(source) or
            source == output or source.is_relative_to(output) or output.is_relative_to(source) or
            output.is_relative_to(fedora) or fedora.is_relative_to(output)):
        parser.error('Input and output trees must not overlap')
    if cert.is_symlink() or not cert.is_file():
        parser.error('An ordinary public DER certificate is required')
    subprocess.run(['openssl', 'x509', '-inform', 'DER', '-in', str(cert), '-noout'],
                   check=True, capture_output=True)
    output.mkdir(parents=True)
    tools = output / 'tools'
    shutil.copytree(ROOT / 'toolkit', tools, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(ROOT / 'runtime/fleet-registry.py', tools / 'fleet-registry.py')
    shutil.copy2(ROOT / 'runtime/storage-target.py', tools / 'storage-target.py')
    shutil.copy2(ROOT / 'config/rtx30-gpu-policy.json', tools / 'rtx30-gpu-policy.json')
    shutil.copy2(ROOT / 'docs/quick-start.md', tools / 'QUICK-START.md')
    shutil.copy2(ROOT / 'docs/user-guide.md', tools / 'DETAILS.md')

    (output / 'usb-config.json').write_text(json.dumps({'schema': 1, 'kind': 'gpu-linux-config', 'root_version': args.root_version}, indent=2) + '\n')
    fleet = runpy.run_path(str(ROOT / 'runtime/fleet-registry.py'))
    # New shared media starts unregistered: never synthesize Windows safety flags.
    registry = fleet['merge']([], 1, args.root_version)
    fleet['deploy'](registry, output)
    shutil.copytree(source, fedora,
                    ignore=lambda folder, names: ['mok-cert'] if 'mok-cert' in names else [])
    shutil.copytree(tools, fedora / 'tools')
    runpy.run_path(str(ROOT / 'scripts/stage-shared-mok-commands.py'))['stage'](cert, fedora / 'mok-cert')
    if args.media_version in ('0.7.5', '0.7.6', '0.7.7', '0.7.8', '0.7.9', '0.8.0', '0.9.0'):
        runpy.run_path(str(ROOT / 'scripts/stage-gui-tools.py'))['stage'](output, fedora, cert)
    manifest = json.loads((fedora / 'fedora-manifest.json').read_text())
    manifest['files'] = sorted(str(path.relative_to(fedora)) for path in fedora.rglob('*') if path.is_file())
    manifest['payload_bytes'] = sum(path.stat().st_size for path in fedora.rglob('*') if path.is_file())
    manifest['shared_toolkit'] = args.media_version
    manifest['media_revision'] = args.media_version
    manifest['gui_connected'] = args.media_version in ('0.7.5', '0.7.6', '0.7.7', '0.7.8', '0.7.9', '0.8.0', '0.9.0')
    (fedora / 'fedora-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(output, fedora)


if __name__ == '__main__':
    main()
