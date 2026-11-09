#!/usr/bin/env python3
"""Refresh a signed shared appliance's boot files, preserving root/state images."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Ordinary image file required')
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def refresh(source, output):
    build = ROOT / 'build'
    source = source.absolute()
    output = output.absolute()
    for path in (source, output):
        if '..' in path.parts or not path.is_relative_to(build) or path == build:
            raise ValueError('Use build/ directories')
        for parent in (path, *path.parents):
            if parent == ROOT:
                break
            if parent.is_symlink():
                raise ValueError('Symlink paths are forbidden')
    if output.exists() or source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError('Choose a new, non-overlapping output directory')
    tree = source / 'usb-tree'
    manifest = json.loads((tree / 'gpu-linux/master.json').read_text())
    configured = json.loads((ROOT / 'config/release.json').read_text())['root_version']
    if (manifest.get('version') not in ('0.9.0-shared', configured) or manifest.get('shared_registry') is not True or
            manifest.get('secure_boot_signed_artifacts') is not True):
        raise ValueError('A signed shared 0.9.0 appliance is required')
    images = {'root.img': manifest['sha256'], 'state.img': manifest['persistent_state']['sha256']}
    for name, expected in images.items():
        if sha(tree / 'gpu-linux' / name) != expected:
            raise ValueError('Original image checksum mismatch: ' + name)
    output.mkdir(parents=True)
    for name in ('usb-tree', 'root-tree'):
        subprocess.run(['cp', '-a', '--reflink=auto', '--sparse=always', '--',
                        str(source / name), str(output / name)], check=True)
    stage, tree = output / 'root-tree', output / 'usb-tree'
    moddir = stage / 'usr/lib/dracut/modules.d/95gpu-root'
    if not moddir.resolve().is_relative_to(stage):
        raise ValueError('Unsafe dracut module directory')
    shutil.copytree(ROOT / 'runtime/dracut/95gpu-root', moddir, dirs_exist_ok=True)
    for name in ('gpu-root.py', 'fleet-registry.py', 'storage-target.py', 'root-maintenance.py',
                 'persistent-state.py', 'replace-root.py', 'no-gpu-test.py', 'release-info.py', 'detach-loop.py',
                 'gpu-diagnostic.py', 'gpu-unlock-diagnostic.py'):
        shutil.copy2(ROOT / 'runtime' / name, moddir / name)
    command = ['bwrap', '--unshare-all', '--uid', '0', '--gid', '0', '--die-with-parent',
               '--bind', str(stage), '/', '--bind', str(tree), '/out', '--proc', '/proc',
               '--dev', '/dev', '--tmpfs', '/tmp', '--clearenv', '--setenv', 'PATH',
               '/usr/sbin:/usr/bin:/sbin:/bin', '--setenv', 'HOME', '/root', '--setenv', 'LC_ALL', 'C', '--']
    subprocess.run(command + ['dracut', '--force', '--tmpdir', '/tmp', '--no-hostonly',
                   '--no-hostonly-cmdline', '--no-compress', '--reproducible', '--modules',
                   'bash systemd systemd-initrd dracut-systemd kernel-modules gpu-root shutdown',
                   '--omit', 'network network-legacy network-manager crypt lvm mdraid resume',
                   '/out/boot/initramfs.img', manifest['kernel_release']], check=True)
    # Inspection sysroot only: root.img's embedded shutdown archive stays intact.
    shutil.copy2(tree / 'boot/initramfs.img', stage / ('boot/initramfs-' + manifest['kernel_release'] + '.img'))
    menu = runpy.run_path(str(ROOT / 'scripts/generate-boot-menu.py'))
    text = menu['generate'](manifest['usb_data_uuid'], {}, shared_registry=True)
    text += menu['generate_fedora_entry']('F044-0107')
    for name in ('boot/grub/grub.cfg', 'EFI/BOOT/grub.cfg', 'EFI/debian/grub.cfg'):
        path = tree / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Expected ordinary signed GRUB configuration')
        path.write_text(text)
    for name, expected in images.items():
        if sha(tree / 'gpu-linux' / name) != expected or sha(source / 'usb-tree/gpu-linux' / name) != expected:
            raise ValueError('Image changed during boot refresh: ' + name)
    if (tree / 'gpu-linux/master.json').read_bytes() != (source / 'usb-tree/gpu-linux/master.json').read_bytes():
        raise ValueError('Master manifest changed')
    (output / 'boot-refresh.json').write_text(json.dumps({
        'version': manifest['version'], 'unchanged_images': images,
        'root_regenerated': False, 'hardware_tested': False}, indent=2) + '\n')
    print('Boot refreshed; root.img, state.img and master.json are unchanged.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='existing generated appliance directory')
    parser.add_argument('--output', type=Path, required=True, help='new build/ appliance directory')
    args = parser.parse_args()
    refresh(args.source, args.output)
