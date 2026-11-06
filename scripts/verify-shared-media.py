#!/usr/bin/env python3
"""Independently read back generated file artifacts; never access host block devices."""
import argparse
import hashlib
import json
import runpy
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image-directory', type=Path, default=ROOT / 'build/usb-image')
    parser.add_argument('--config-tree', type=Path, default=ROOT / 'build/shared-usb-tools')
    parser.add_argument('--fedora-tree', type=Path, default=ROOT / 'build/fedora-live-tools')
    parser.add_argument('--tree', type=Path, default=ROOT / 'build/appliance/usb-tree')
    parser.add_argument('--sysroot', type=Path, required=True, help='generated root-tree with image inspection tools')
    args = parser.parse_args()
    output = args.image_directory.resolve()
    if not output.is_relative_to(ROOT / 'build'):
        raise ValueError('build/ artifact directory required')
    tree = args.tree.resolve()
    if not tree.is_relative_to(ROOT / 'build'):
        raise ValueError('build/ appliance tree required')
    config, fedora = args.config_tree.resolve(), args.fedora_tree.resolve()
    if any(not p.is_relative_to(ROOT / 'build') for p in (config, fedora)):
        raise ValueError('config and Fedora trees must be below build/')
    image = output / 'gpu-linux-usb.img'
    if image.is_symlink() or not image.is_file():
        raise ValueError('Regular image file required')
    actual = sha(image)
    if actual != (output / 'SHA256SUMS').read_text().split()[0]:
        raise ValueError('Independent image checksum mismatch')
    table = json.loads((output / 'partition-table.json').read_text())['partitiontable']
    matches = []
    with image.open('rb') as stream:
        for name, partition in zip(('efi.img', 'usb-data.img', 'usb-logs.img', 'fedora-live.img'), table['partitions'], strict=True):
            path = output / name
            if path.is_symlink() or path.stat().st_size != partition['size'] * 512:
                raise ValueError('Partition artifact size mismatch')
            stream.seek(partition['start'] * 512)
            with path.open('rb') as part:
                while block := part.read(8 * 1024 * 1024):
                    if stream.read(len(block)) != block:
                        raise ValueError('Raw image partition differs: ' + name)
            matches.append(name)
    print('Independent SHA and all raw partition bytes match', flush=True)
    sysroot = args.sysroot.resolve()
    if not sysroot.is_relative_to(ROOT / 'build') or not (sysroot / 'usr/sbin/sfdisk').is_file():
        raise ValueError('Generated sysroot with sfdisk required below build/')
    cmd = ['bwrap', '--unshare-all', '--ro-bind', str(sysroot), '/',
           '--bind', str(output), '/out', '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp',
           '--clearenv', '--setenv', 'PATH', '/usr/sbin:/usr/bin:/sbin:/bin', '--']
    subprocess.run(cmd + ['sfdisk', '--verify', '/out/gpu-linux-usb.img'], check=True)
    for name in ('efi.img', 'usb-logs.img', 'fedora-live.img'):
        subprocess.run(cmd + ['fsck.fat', '-n', '/out/' + name], check=True)
    checks = []

    def verify(name, relative, source):
        proc = subprocess.Popen(cmd + ['mtype', '-i', '/out/' + name, '::/' + relative], stdout=subprocess.PIPE)
        h = hashlib.sha256()
        while block := proc.stdout.read(8 * 1024 * 1024):
            h.update(block)
        if proc.wait() or h.hexdigest() != sha(source):
            raise ValueError('FAT readback mismatch: ' + relative)
        checks.append({'image': name, 'path': relative, 'sha256': h.hexdigest()})
    for name in ('BOOTX64.EFI', 'mmx64.efi', 'grubx64.efi', 'grub.cfg', 'signing-cert.der'):
        verify('efi.img', 'EFI/BOOT/' + name, tree / 'EFI/BOOT' / name)
    verify('usb-logs.img', 'signing-cert.der', tree / 'EFI/BOOT/signing-cert.der')
    for path in config.rglob('*'):
        if path.is_file():
            verify('usb-logs.img', str(path.relative_to(config)), path)
    for folder in ('tools', 'mok-cert', 'gui'):
        for path in (fedora / folder).rglob('*'):
            if path.is_file():
                verify('fedora-live.img', str(path.relative_to(fedora)), path)
    if (fedora / 'gui').is_dir():
        for name in ('start-windows-gui.cmd', 'start-windows-gui-debug.cmd', 'start-fedora-gui.sh', 'gpu-linux.desktop', 'gui-launcher-overlay.json'):
            verify('fedora-live.img', name, fedora / name)
        overlay = json.loads((fedora / 'gui-launcher-overlay.json').read_text())
        sources = {
            'var/lib/dracut/hooks/pre-pivot/99-gpu-gui-launchers.sh': 'fedora-launchers-hook.sh',
            'usr/lib/gpu-linux-launchers-runtime.sh': 'fedora-launchers-runtime.sh',
            'usr/lib/gpu-linux-autostart.desktop': 'gpu-linux-autostart.desktop',
            'usr/lib/gpu-linux.desktop': 'gpu-linux.desktop',
        }
        if not overlay.get('autostart') or overlay['files'] != list(sources):
            raise ValueError('Unexpected Fedora launcher overlay files')
        files = [(name, (ROOT / 'gui' / source).read_bytes(), 0o100755 if name.endswith('.sh') else 0o100644)
                 for name, source in sources.items()]
        archive = runpy.run_path(str(ROOT / 'scripts/stage-gui-tools.py'))['cpio'](files)
        with (fedora / 'boot/x86_64/loader/initrd').open('rb') as stream:
            original = hashlib.sha256()
            left = overlay['original_initrd_bytes']
            while left:
                block = stream.read(min(left, 8 * 1024**2))
                if not block:
                    raise ValueError('Truncated original Fedora initrd')
                original.update(block)
                left -= len(block)
            padding = (-stream.tell()) % 4
            if stream.read(padding) != b'\0' * padding or stream.read() != archive:
                raise ValueError('Fedora launcher cpio suffix differs from source')
            if original.hexdigest() != overlay['original_initrd_sha256']:
                raise ValueError('Original Fedora initrd bytes changed')
    listing = subprocess.check_output(cmd + ['mdir', '-b', '-i', '/out/fedora-live.img', '::/mok-cert/*'], text=True)
    if any(token in listing.lower() for token in ('.der', '.pem', '.key')):
        raise ValueError('MOK command folder unexpectedly contains a key/certificate')
    # Official Fedora boot/live files must remain byte-identical to extracted inputs.
    for name in ('EFI/BOOT/BOOTX64.EFI', 'EFI/BOOT/grubx64.efi', 'EFI/BOOT/mmx64.efi',
                 'boot/x86_64/loader/linux', 'boot/x86_64/loader/initrd', 'LiveOS/squashfs.img'):
        if (fedora / name).is_file():
            verify('fedora-live.img', name, fedora / name)
    for path in ('/gpu-linux/root.img', '/boot/initramfs.img', '/boot/grub/grub.cfg'):
        source = tree / path.lstrip('/')
        proc = subprocess.Popen(cmd + ['debugfs', '-R', 'cat ' + path, '/out/usb-data.img'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        h = hashlib.sha256()
        while block := proc.stdout.read(8 * 1024 * 1024):
            h.update(block)
        if proc.wait() or h.hexdigest() != sha(source):
            raise ValueError('Master filesystem readback mismatch: ' + path)
        checks.append({'image': 'usb-data.img', 'path': path, 'sha256': h.hexdigest()})
    (output / 'shared-media-verification.json').write_text(json.dumps({
        'sha256': actual, 'bytes': image.stat().st_size, 'raw_partitions_match': matches,
        'gpt_valid': True, 'fat_filesystems_valid': True, 'checks': checks,
        'mok_folder_certificate_free': True, 'hardware_tested': False,
        'gui_connected': (fedora / 'gui').is_dir(), 'native_gui_tested': False,
    }, indent=2) + '\n')
    print('Shared media readback verified:', actual, flush=True)


if __name__ == '__main__':
    main()
