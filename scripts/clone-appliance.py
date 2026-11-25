#!/usr/bin/env python3
"""Reuse a validated release root without regenerating root or persistent state."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import stat
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]

def write_copied_metadata(path, text):
    """Edit only the fresh copy, retaining its original read-only mode."""
    if not path.is_file() or any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('Ordinary copied metadata required')
    mode = stat.S_IMODE(path.stat().st_mode)
    path.chmod(mode | stat.S_IWUSR)
    try:
        path.write_text(text)
    finally:
        path.chmod(mode)

def digest(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Ordinary appliance artifact required')
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def clone(source, output, profile):
    fresh = runpy.run_path(str(ROOT / 'scripts/prepare-shared-usb-tools.py'))['fresh_output']
    output = fresh(output)
    source = source.absolute()
    if profile not in ('standard', 'loan') or source.is_symlink() or not source.is_relative_to(ROOT / 'build'):
        raise ValueError('Workspace appliance and known profile required')
    if any(p.is_symlink() for p in (source, *source.parents)):
        raise ValueError('Symlink appliance source refused')
    if source == output or source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError('Appliance trees overlap')
    manifest_path = source / 'usb-tree/gpu-linux/master.json'
    if any(p.is_symlink() for p in (manifest_path, *manifest_path.parents)):
        raise ValueError('Symlink appliance metadata refused')
    manifest = json.loads(manifest_path.read_text())
    release = json.loads((ROOT / 'config/release.json').read_text())
    for key in ('version', 'boot_abi', 'state_schema'):
        if manifest[key] != release[key]:
            raise ValueError('Reusable appliance differs from configured release')
    for name, meta in (('root.img', manifest), ('state.img', manifest['persistent_state'])):
        path = source / 'usb-tree/gpu-linux' / name
        if path.stat().st_size != meta['size'] or digest(path) != meta['sha256']:
            raise ValueError('Reusable root/state checksum mismatch')
    # Validation reads only ordinary files and runs tools in a device-free sandbox.
    evidence = fresh(output.with_name(output.name + '-source-validation.json'))
    subprocess.run(['python3', str(ROOT / 'scripts/validate-appliance.py'), str(source / 'usb-tree'),
                    '--output', str(evidence)], check=True)
    output.mkdir(parents=True)
    subprocess.run(['cp', '-a', '--reflink=auto', '--sparse=always', str(source) + '/.', str(output)], check=True)
    tree = output / 'usb-tree'
    if 'boot_integrity_signer_sha256' in manifest:
        # The clone has a new MASTER identity and menu. Its authorization
        # must be issued for those new bytes, never carried from the source.
        for name in ('boot-integrity.json', 'boot-integrity.sig'):
            (tree / 'gpu-linux' / name).unlink()
    manifest['usb_data_uuid'] = str(uuid.uuid4())
    write_copied_metadata(tree / 'gpu-linux/master.json', json.dumps(manifest, indent=2) + '\n')
    menu = runpy.run_path(str(ROOT / 'scripts/generate-boot-menu.py'))
    text = menu['generate'](manifest['usb_data_uuid'], {}, shared_registry=True)
    text = text.replace('set timeout=15', 'set timeout=60', 1)
    if profile == 'standard':
        text += menu['generate_fedora_entry']('F044-0107')
    write_copied_metadata(tree / 'boot/grub/grub.cfg', text)
    for relative in ('EFI/BOOT/grub.cfg', 'EFI/debian/grub.cfg'):
        write_copied_metadata(tree / relative, text)
    print('Reused release root/state; generated a distinct MASTER identity:', output)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', choices=('standard', 'loan'), required=True)
    args = parser.parse_args()
    clone(args.source, args.output, args.profile)
