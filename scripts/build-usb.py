#!/usr/bin/env python3
"""Prepare fixed inputs, then assemble a file image from an owner-signed bundle."""
import argparse
import json
from pathlib import Path
import re
import runpy
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RELEASE = json.loads((ROOT / 'config/release.json').read_text())
LAYOUT = json.loads((ROOT / 'config/usb-layout.json').read_text())
VERSION = RELEASE['version']
UPDATE_BUNDLE = runpy.run_path(str(ROOT / 'scripts/prepare-update-bundle.py'))
PREPARED = ('debian-root', 'appliance-root', 'nvidia-runtime-615.71.10', 'pair-v1.0.0-headless',
            'pair-llamacpp-b11146', 'candidate-network-overlay-v1', 'pair-runtime-overlay-v1',
            'efi-inputs', 'assets-gpu-unlock-2.0.0', 'fedora-live', 'runtime-root')


def python(script, *args):
    return [sys.executable, str(ROOT / 'scripts' / script), *map(str, args)]


def update_output(args):
    return args.update_output or UPDATE_BUNDLE['archive_path'](
        getattr(args, 'source_repository', ROOT), VERSION,
        getattr(args, 'profile', 'standard'), args.update_sequence)


def previous_archive(repository, profile, version):
    def version_key(name):
        if re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', name):
            return tuple(map(int, name.split('.')))
        return None

    current = version_key(version)
    archive = Path(repository).absolute() / 'build/update-bundles'
    if current is None or not archive.exists():
        return None
    UPDATE_BUNDLE['ordinary'](archive, True)
    candidates = []
    for version_dir in archive.iterdir():
        key = version_key(version_dir.name)
        if key is None or key >= current:
            continue
        profile_dir = version_dir / profile
        if not profile_dir.exists():
            continue
        UPDATE_BUNDLE['ordinary'](profile_dir, True)
        for folder in profile_dir.iterdir():
            match = re.fullmatch(r'sequence-([1-9][0-9]*)', folder.name)
            if match and (folder / 'manifest.json').exists() and (folder / 'SHA256SUMS').exists():
                candidates.append((key, int(match[1]), folder))
    if not candidates:
        return None
    _, sequence, folder = max(candidates)
    manifest = UPDATE_BUNDLE['ordinary'](folder / 'manifest.json')
    checksum = UPDATE_BUNDLE['ordinary'](folder / 'SHA256SUMS').read_text().splitlines()
    if not checksum or checksum[0].split() != [UPDATE_BUNDLE['digest'](manifest), 'manifest.json']:
        raise ValueError('Previous archived manifest checksum mismatch')
    metadata = UPDATE_BUNDLE['read_json'](manifest)
    if metadata['update_sequence'] != sequence or metadata['release']['version'] != folder.parent.parent.name:
        raise ValueError('Previous archived manifest does not match its version and sequence')
    return folder


def accepted_update_arguments(args):
    images = args.accepted_image_directory
    bundles = args.accepted_bundle
    if not (images or bundles or getattr(args, 'no_previous_source', False)
            or getattr(args, 'accepted_worker_merge_fix', None)):
        profile = getattr(args, 'profile', 'standard')
        previous = previous_archive(getattr(args, 'source_repository', ROOT), profile, VERSION)
        if previous:
            if int(previous.name.removeprefix('sequence-')) >= args.update_sequence:
                raise ValueError('Update sequence must increase beyond the previous archived bundle')
            if profile == 'standard':
                bundles = [previous / 'manifest.json']
            else:
                images = [previous]
    return ([value for path in images for value in ('--accepted-image-directory', path)]
            + [value for path in bundles for value in ('--accepted-bundle', path)])


def prepare_commands(profile='standard'):
    commands = [
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

    if profile == 'loan':
        commands = [cmd for cmd in commands if 'prepare-fedora-live.py' not in str(cmd)]
    return commands


def image_commands(args):
    profile = getattr(args, 'profile', 'standard')
    manager = getattr(args, 'manager_output', None) or args.output.with_name(args.output.name + '-manager')
    cert = args.signed_modules / 'signing-cert.der'
    tree, sysroot = args.appliance_output / 'usb-tree', args.appliance_output / 'root-tree'
    commands = [
        python('fetch-inputs.py', '--offline'), python('verify-repository-inputs.py'),
        python('prepare-upstream.py', '--offline'),
        python('verify-signed-module-bundle.py', args.signed_modules),
        python('prepare-shared-usb-tools.py', '--fedora-source', 'build/fedora-live',
               '--certificate', cert, '--output', args.tools_output, '--fedora-output', args.fedora_output,
               '--media-version', RELEASE['version'], '--root-version', VERSION,
               '--profile', profile, '--manager-output', manager),
        python('build-root-appliance.py', '--source-root', 'build/runtime-root', '--output', args.appliance_output,
               '--version', VERSION, '--size-mib', '6144', '--shared-registry', '--local-console',
               '--network-overlay', 'build/candidate-network-overlay-v1', '--pair-runtime',
               '--signed-modules', args.signed_modules, '--efi-root', 'build/efi-inputs',
               '--fedora-tree', args.fedora_output),
        python('validate-appliance.py', tree, '--output', args.appliance_output / 'validation.json'),
        python('assemble-usb-image.py', '--profile', profile, '--tree', tree, '--sysroot', sysroot, '--output', args.output,
               '--data-mib', LAYOUT['master_size_mib'], '--log-mib', '256', '--config-tree', args.tools_output,
               '--fedora-tree', args.fedora_output),
        python('verify-shared-media.py', '--profile', profile, '--tree', tree, '--sysroot', sysroot,
               '--image-directory', args.output, '--config-tree', args.tools_output,
               '--fedora-tree', args.fedora_output),
    ]
    if profile == 'loan':
        for command in commands:
            for flag in ('--fedora-source', '--fedora-output', '--fedora-tree'):
                if flag in command:
                    index = command.index(flag)
                    del command[index:index+2]
    reuse = getattr(args, 'reuse_appliance', None)
    if reuse is not None:
        index = next(i for i, cmd in enumerate(commands) if cmd[1].endswith('/build-root-appliance.py'))
        commands[index] = python('clone-appliance.py', '--source', reuse, '--output', args.appliance_output, '--profile', profile)
    if getattr(args, 'update_bundle', False):
        record = args.output.with_name(args.output.name + '-source.json')
        inspection = args.output.with_name(args.output.name + '-inspection-tools')
        bundle = update_output(args)
        source_args = [value for path in args.source_extra for value in ('--source-extra', path)]
        accepted_args = accepted_update_arguments(args)
        if getattr(args, 'accepted_worker_merge_fix', None) is not None:
            accepted_args += ['--accepted-worker-merge-fix', args.accepted_worker_merge_fix]
        commands = [python('record-build-source.py', '--output', record, '--repository', getattr(args, 'source_repository', ROOT), *source_args)] + commands
        commands += [python('prepare-updater-inspection.py', '--offline', '--output', inspection),
                     python('prepare-update-bundle.py', '--image-directory', args.output,
                            '--source-record', record, '--inspection-sysroot', inspection,'--signed-modules',args.signed_modules,
                            '--repository', getattr(args, 'source_repository', ROOT), '--output', bundle, '--publish-image',
                            '--profile', profile, '--update-sequence', args.update_sequence, *accepted_args)]
    return commands


def check_host():
    check = runpy.run_path(str(ROOT / 'scripts/check-environment.py'))
    report = check['inspect_environment'](ROOT)
    if not report['build_prerequisites_present']:
        raise ValueError('\n'.join(report['missing']))


def validate_image_outputs(args):
    fresh = runpy.run_path(str(ROOT / 'scripts/prepare-firmware.py'))['fresh_output']
    outputs = [args.output, args.appliance_output, args.tools_output,
               getattr(args, 'manager_output', None) or args.output.with_name(args.output.name + '-manager')]
    if getattr(args, 'profile', 'standard') == 'standard':
        outputs.append(args.fedora_output)
    if getattr(args, 'update_bundle', False):
        outputs += [args.output.with_name(args.output.name + '-source.json'),
                    args.output.with_name(args.output.name + '-inspection-tools')]
    paths = [fresh(path) for path in outputs]
    if getattr(args, 'update_bundle', False):
        paths.append(UPDATE_BUNDLE['bundle_destination'](
            update_output(args), getattr(args, 'source_repository', ROOT), VERSION,
            getattr(args, 'profile', 'standard'), args.update_sequence))
    for i, left in enumerate(paths):
        for right in paths[i+1:]:
            if left == right or left.is_relative_to(right) or right.is_relative_to(left):
                raise ValueError('Image output directories must not overlap')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('prepare', 'image'))
    parser.add_argument('--profile', choices=('standard', 'loan'), default='standard')
    parser.add_argument('--reuse-appliance', type=Path, help='reuse the validated root/state from this same release')
    parser.add_argument('--manager-output', type=Path)
    parser.add_argument('--plan', action='store_true', help='print commands without running or writing')
    parser.add_argument('--signed-modules', type=Path, default=ROOT / 'build/signed-modules')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/usb-image')
    parser.add_argument('--appliance-output', type=Path, default=ROOT / 'build/appliance')
    parser.add_argument('--tools-output', type=Path, default=ROOT / 'build/shared-usb-tools')
    parser.add_argument('--fedora-output', type=Path, default=ROOT / 'build/fedora-live-tools')
    parser.add_argument('--update-bundle', action='store_true', help='also generate the pinned updater manifest and managed payload')
    parser.add_argument('--update-output', type=Path)
    parser.add_argument('--update-sequence', type=int, default=1)
    parser.add_argument('--accepted-image-directory', type=Path, action='append', default=[])
    parser.add_argument('--accepted-bundle', type=Path, action='append', default=[])
    parser.add_argument('--no-previous-source', action='store_true',
                        help='do not automatically accept the previous archived release')
    parser.add_argument('--accepted-worker-merge-fix', type=Path,
                        help='explicit source record for the pinned 0.9.3 Windows worker maintenance fix')
    parser.add_argument('--source-repository', type=Path, default=ROOT, help='Git repository used for build-source provenance')
    parser.add_argument('--source-extra', action='append', default=[], help='explicitly include an untracked public source path')
    args = parser.parse_args()
    if args.update_sequence < 1:
        parser.error('Positive update sequence required')
    commands = prepare_commands(args.profile) if args.phase == 'prepare' else image_commands(args)
    if args.plan:
        for command in commands:
            print(shlex.join(command))
        return
    check_host()
    if args.phase == 'prepare':
        fresh = runpy.run_path(str(ROOT / 'scripts/prepare-firmware.py'))['fresh_output']
        for name in PREPARED:
            if args.profile != 'loan' or name != 'fedora-live':
                fresh(ROOT / 'build' / name)
        for name in ('modules/gpu-unlock-2.0.0/6.12.113+deb13-amd64', 'nvidia-modules/615.71.10/6.12.113+deb13-amd64'):
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
        if args.update_bundle:
            bundle = update_output(args)
            print(f'Validated file image: {bundle / "images/gpu-linux-usb.img"}')
            print(f'Validated update manifest: {bundle / "manifest.json"}')
        else:
            print(f'Validated file image: {args.output / "gpu-linux-usb.img"}')
        print('No physical USB was written; native boot qualification is still required.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f'Build stopped; preserve existing outputs and inspect the failed stage: {error}')
