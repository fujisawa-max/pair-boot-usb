#!/usr/bin/env python3
"""Prepare fixed inputs, then assemble a file image from an owner-signed bundle."""
import argparse
import json
from pathlib import Path
import runpy
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.8.0-shared'
PREPARED = ('debian-root', 'appliance-root', 'nvidia-runtime-615.71.10', 'pair-v1.0.0-headless',
            'pair-llamacpp-b11146', 'candidate-network-overlay-v1', 'pair-runtime-overlay-v1',
            'efi-inputs', 'assets', 'fedora-live', 'runtime-root')


def python(script, *args):
    return [sys.executable, str(ROOT / 'scripts' / script), *map(str, args)]


def prepare_commands():
    return [
        python('fetch-inputs.py'),
        python('verify-repository-inputs.py'),
        python('prepare-upstream.py'),
        python('prepare-debian-build.py', '--offline', '--extra-lock', 'inputs/debian-build-tools-inputs.json',
               '--output', 'build/debian-root'),
        python('prepare-debian-build.py', '--offline', '--lock', 'inputs/debian-runtime-inputs.json',
               '--output', 'build/appliance-root'),
        ['sh', 'scripts/build-kernel-module.sh'],
        ['sh', 'scripts/build-nvidia-open-module.sh'],
        python('verify-nvidia-modules.py'),
        python('prepare-nvidia-runtime.py', '--output', 'build/nvidia-runtime-615.71.10'),
        python('prepare-pair-assets.py', '--output', 'build/pair-v1.0.0-headless'),
        python('prepare-pair-llamacpp.py', '--output', 'build/pair-llamacpp-b11146'),
        python('prepare-candidate-network.py'), python('prepare-pair-runtime.py'),
        python('prepare-signed-efi.py'), python('prepare-firmware.py'),
        python('prepare-fedora-live.py', '.cache/fedora/Fedora-Workstation-Live-44-1.7.x86_64.iso',
               '--output', 'build/fedora-live'),
        python('stage-pilot-root.py', '--output', 'build/runtime-root'),
    ]


def image_commands(args):
    cert = args.signed_modules / 'signing-cert.der'
    tree, sysroot = args.appliance_output / 'usb-tree', args.appliance_output / 'root-tree'
    return [
        python('fetch-inputs.py', '--offline'), python('verify-repository-inputs.py'),
        python('prepare-upstream.py', '--offline'),
        python('verify-signed-module-bundle.py', args.signed_modules),
        python('prepare-shared-usb-tools.py', '--fedora-source', 'build/fedora-live',
               '--certificate', cert, '--output', args.tools_output, '--fedora-output', args.fedora_output,
               '--media-version', '0.8.0', '--root-version', VERSION),
        python('build-root-appliance.py', '--source-root', 'build/runtime-root', '--output', args.appliance_output,
               '--version', VERSION, '--size-mib', '6144', '--shared-registry', '--local-console',
               '--network-overlay', 'build/candidate-network-overlay-v1', '--pair-runtime',
               '--signed-modules', args.signed_modules, '--efi-root', 'build/efi-inputs',
               '--fedora-tree', args.fedora_output),
        python('validate-appliance.py', tree, '--output', args.appliance_output / 'validation.json'),
        python('assemble-usb-image.py', '--tree', tree, '--sysroot', sysroot, '--output', args.output,
               '--data-mib', '8192', '--log-mib', '256', '--config-tree', args.tools_output,
               '--fedora-tree', args.fedora_output),
        python('verify-shared-media.py', '--tree', tree, '--sysroot', sysroot,
               '--image-directory', args.output, '--config-tree', args.tools_output,
               '--fedora-tree', args.fedora_output),
    ]


def check_host():
    check = runpy.run_path(str(ROOT / 'scripts/check-environment.py'))
    report = check['inspect_environment'](ROOT)
    if not report['build_prerequisites_present']:
        raise ValueError('\n'.join(report['missing']))


def validate_image_outputs(args):
    fresh = runpy.run_path(str(ROOT / 'scripts/prepare-firmware.py'))['fresh_output']
    paths = [fresh(path) for path in (args.output, args.appliance_output, args.tools_output, args.fedora_output)]
    for i, left in enumerate(paths):
        for right in paths[i+1:]:
            if left == right or left.is_relative_to(right) or right.is_relative_to(left):
                raise ValueError('Image output directories must not overlap')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('prepare', 'image'))
    parser.add_argument('--plan', action='store_true', help='print commands without running or writing')
    parser.add_argument('--signed-modules', type=Path, default=ROOT / 'build/signed-modules')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/usb-image')
    parser.add_argument('--appliance-output', type=Path, default=ROOT / 'build/appliance')
    parser.add_argument('--tools-output', type=Path, default=ROOT / 'build/shared-usb-tools')
    parser.add_argument('--fedora-output', type=Path, default=ROOT / 'build/fedora-live-tools')
    args = parser.parse_args()
    commands = prepare_commands() if args.phase == 'prepare' else image_commands(args)
    if args.plan:
        for command in commands:
            print(shlex.join(command))
        return
    check_host()
    if args.phase == 'prepare':
        fresh = runpy.run_path(str(ROOT / 'scripts/prepare-firmware.py'))['fresh_output']
        for name in PREPARED:
            fresh(ROOT / 'build' / name)
        for name in ('modules/6.12.113+deb13-amd64', 'nvidia-modules/615.71.10/6.12.113+deb13-amd64'):
            fresh(ROOT / 'build' / name)
    else:
        validate_image_outputs(args)
        # Reject a wrong or incomplete public bundle before any output exists.
        runpy.run_path(str(ROOT / 'scripts/verify-signed-module-bundle.py'))['verify'](args.signed_modules)
    for number, command in enumerate(commands, 1):
        print(f'[{number}/{len(commands)}] {shlex.join(command)}', flush=True)
        log = ROOT / 'build' / f'{args.phase}-{number:02d}.log'
        log.parent.mkdir(exist_ok=True)
        if log.is_symlink():
            raise ValueError('Refusing symlink build log')
        print(f'Log: {log.relative_to(ROOT)}', flush=True)
        with log.open('w') as stream:
            result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            print('\n'.join(log.read_text(errors='replace').splitlines()[-30:]), file=sys.stderr)
            raise ValueError(f'Stage {number} failed; see {log.relative_to(ROOT)}')
    if args.phase == 'prepare':
        print('Preparation complete. The owner must create or select an external key and sign the six module copies.')
        print('See docs/secure-boot.md; then run: python3 scripts/build-usb.py image')
    else:
        print(f'Validated file image: {args.output / "gpu-linux-usb.img"}')
        print('No physical USB was written; native boot qualification is still required.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f'Build stopped; preserve existing outputs and inspect the failed stage: {error}')
