#!/usr/bin/env python3
"""Prepare and verify detached authorization for a staged PAIR BOOT tree."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = runpy.run_path(str(ROOT / 'runtime/boot-integrity.py'))
ARTIFACTS = runpy.run_path(str(ROOT / 'scripts/updater-artifacts.py'))
ordinary = ARTIFACTS['ordinary']
canonical = ARTIFACTS['canonical']
LIMIT = 64 * 1024
MENUS = ('boot/grub/grub.cfg', 'EFI/BOOT/grub.cfg', 'EFI/debian/grub.cfg')
FILES = (
    'EFI/BOOT/BOOTX64.EFI', 'EFI/BOOT/grubx64.efi', 'EFI/BOOT/mmx64.efi',
    'EFI/BOOT/signing-cert.der', *MENUS, 'boot/vmlinuz', 'boot/initramfs.img',
    'gpu-linux/master.json', 'gpu-linux/root.img',
)
VERITY_FILES = ('gpu-linux/root.verity', 'gpu-linux/root.verity.json')
VERITY_BLOCK = 4096


def prepare_verity(tree, output):
    tree = ordinary(tree, directory=True)
    root = tree / 'gpu-linux/root.img'
    metadata = json.loads(bounded(tree / 'gpu-linux/master.json'), object_pairs_hook=unique_fields)
    source = record(root)
    if (not isinstance(metadata, dict)
            or source != {'bytes': metadata.get('size'), 'sha256': metadata.get('sha256')}
            or source['bytes'] < 2 * VERITY_BLOCK or source['bytes'] % VERITY_BLOCK):
        raise ValueError('Verity requires a block-aligned root matching MASTER metadata')
    destination = ordinary(output.parent, directory=True) / output.name
    if destination.exists() or destination.is_symlink():
        raise ValueError('Use a fresh verity output directory')
    with tempfile.TemporaryDirectory(prefix='.pair-verity-', dir=destination.parent) as folder:
        work = Path(folder)
        hashes, root_hash = work / 'root.verity', work / 'root-hash'
        options = ['--format=1', '--hash=sha256', '--data-block-size=4096', '--hash-block-size=4096']
        subprocess.run(['veritysetup', *options, '--root-hash-file', str(root_hash),
                        'format', str(root), str(hashes)], check=True, capture_output=True)
        digest = bounded(root_hash).decode('ascii').strip()
        if not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('Invalid verity root digest')
        subprocess.run(['veritysetup', 'verify', str(root), str(hashes), digest],
                       check=True, capture_output=True)
        if record(root) != source:
            raise ValueError('Root image changed during verity preparation')
        descriptor = work / 'root.verity.json'
        descriptor.write_bytes(canonical({'schema': 1, 'root': source, 'root_hash': digest,
                                          'hash_image': record(hashes)}))
        destination.mkdir()
        hashes.rename(destination / hashes.name)
        descriptor.rename(destination / descriptor.name)


def open_regular(path):
    path = ordinary(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    stream = os.fdopen(fd, 'rb')
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        stream.close()
        raise ValueError('Expected an ordinary file')
    return stream


def bounded(path):
    with open_regular(path) as stream:
        data = stream.read(LIMIT + 1)
    if not data or len(data) > LIMIT:
        raise ValueError('Invalid boot metadata size')
    return data


def unique_fields(items):
    value = {}
    for key, entry in items:
        if key in value:
            raise ValueError('Duplicate boot metadata field')
        value[key] = entry
    return value


def record(path):
    with open_regular(path) as stream:
        before = os.fstat(stream.fileno())
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = os.fstat(stream.fileno())
    if (before.st_size <= 0 or
            (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
            (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise ValueError(f'Empty or changing boot artifact: {path.name}')
    return {'bytes': before.st_size, 'sha256': digest}


def manifest(tree, version, profile):
    tree = ordinary(tree, directory=True)
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Unified release version required')
    if profile not in ('standard', 'loan'):
        raise ValueError('Unknown boot profile')
    master_data = bounded(tree / 'gpu-linux/master.json')
    master = json.loads(master_data, object_pairs_hook=unique_fields)
    if (not isinstance(master, dict) or master.get('version') != version
            or master.get('secure_boot_signed_artifacts') is not True):
        raise ValueError('Unexpected release or unsigned boot artifacts')
    menus = [bounded(tree / path) for path in MENUS]
    if any(data != menus[0] for data in menus[1:]):
        raise ValueError('Boot menu copies differ')
    fedora_entry = b'menuentry "Fedora Live / MOK and maintenance"' in menus[0]
    if fedora_entry != (profile == 'standard'):
        raise ValueError('Boot menu does not match media profile')
    files = {name: record(tree / name) for name in FILES}
    root = files['gpu-linux/root.img']
    if (files['gpu-linux/master.json']['sha256'] != hashlib.sha256(master_data).hexdigest()
            or any(files[name]['sha256'] != hashlib.sha256(menus[0]).hexdigest() for name in MENUS)):
        raise ValueError('Boot metadata changed during inspection')
    if root != {'bytes': master.get('size'), 'sha256': master.get('sha256')}:
        raise ValueError('Root image differs from master metadata')
    verity_paths = [tree / name for name in VERITY_FILES]
    if any(os.path.lexists(path) for path in verity_paths):
        descriptor = bounded(verity_paths[1])
        verity = json.loads(descriptor, object_pairs_hook=unique_fields)
        hashes = record(verity_paths[0])
        if (not isinstance(verity, dict) or set(verity) != {'schema', 'root', 'root_hash', 'hash_image'}
                or type(verity['schema']) is not int or verity['schema'] != 1 or verity['root'] != root
                or verity['hash_image'] != hashes or not isinstance(verity['root_hash'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', verity['root_hash'])):
            raise ValueError('Verity descriptor does not match the staged images')
        subprocess.run(['veritysetup', 'verify', str(tree / 'gpu-linux/root.img'),
                        str(verity_paths[0]), verity['root_hash']], check=True, capture_output=True)
        files[VERITY_FILES[0]] = hashes
        files[VERITY_FILES[1]] = {'bytes': len(descriptor), 'sha256': hashlib.sha256(descriptor).hexdigest()}
    return canonical({'schema': 1, 'kind': 'pair-boot-integrity',
                      'version': version, 'profile': profile, 'files': files})


def verify(tree, version, profile, signed_manifest, signature, signer):
    tree = ordinary(tree, directory=True)
    signer = ordinary(signer)
    if signer.is_relative_to(tree):
        raise ValueError('Supply the trusted release signer independently of the staged tree')
    payload = bounded(signed_manifest)
    RUNTIME['verify_signature'](payload, bounded(signature), RUNTIME['signer_pem'](signer))
    if payload != manifest(tree, version, profile):
        raise ValueError('Signed boot manifest differs from the requested tree, release, or profile')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    prepare = commands.add_parser('prepare')
    check = commands.add_parser('verify')
    verity = commands.add_parser('prepare-verity')
    verity.add_argument('--tree', type=Path, required=True)
    verity.add_argument('--output', type=Path, required=True)
    for command in (prepare, check):
        command.add_argument('--tree', type=Path, required=True)
        command.add_argument('--version', required=True)
        command.add_argument('--profile', choices=('standard', 'loan'), required=True)
    prepare.add_argument('--output', type=Path, required=True)
    check.add_argument('--manifest', type=Path, required=True)
    check.add_argument('--signature', type=Path, required=True)
    check.add_argument('--trusted-signer', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'prepare-verity':
        prepare_verity(args.tree, args.output)
        print('Prepared and verified the dm-verity hash image and descriptor.')
    elif args.action == 'prepare':
        destination = ordinary(args.output.parent, directory=True) / args.output.name
        if destination.exists() or destination.is_symlink():
            raise ValueError('Use a fresh manifest output path')
        payload = manifest(args.tree, args.version, args.profile)
        with destination.open('xb') as stream:
            stream.write(payload)
        print('Prepared staged boot integrity manifest for the release signer.')
    else:
        verify(args.tree, args.version, args.profile, args.manifest, args.signature, args.trusted_signer)
        print('Pinned release signature and staged boot artifact hashes verified.')


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(f'Boot integrity command failed: {error.cmd[0]} (status {error.returncode})')
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        sys.exit(f'Boot integrity manifest operation stopped: {error}')
