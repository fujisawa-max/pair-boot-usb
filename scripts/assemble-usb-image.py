#!/usr/bin/env python3
"""Assemble regular GPT/FAT/ext4 image files under build/; never write a device."""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MIB = 1024 * 1024
BLOCK = 4096
MASTER_RESERVE = 512 * MIB
DEFAULT_MASTER_MIB = json.loads((ROOT / 'config/usb-layout.json').read_text())['master_size_mib']


def sparse_bytes(path):
    """Count data extents in logical blocks, independently of compression/reflinks."""
    with path.open('rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        offset = used = 0
        while offset < size:
            try:
                data = os.lseek(stream.fileno(), offset, os.SEEK_DATA)
            except OSError as error:
                if error.errno == errno.ENXIO:
                    return used
                if error.errno in (errno.EINVAL, errno.ENOTSUP):
                    # Unknown hole mapping: keep the conservative logical-size bound.
                    return (size + BLOCK - 1) // BLOCK * BLOCK
                raise
            try:
                hole = os.lseek(stream.fileno(), data, os.SEEK_HOLE)
            except OSError as error:
                if error.errno in (errno.EINVAL, errno.ENOTSUP, errno.ENXIO):
                    return (size + BLOCK - 1) // BLOCK * BLOCK
                raise
            if data < offset or hole <= data:
                raise ValueError('Invalid sparse extent mapping')
            used += ((min(hole, size) + BLOCK - 1) // BLOCK - data // BLOCK) * BLOCK
            offset = hole
        return used


def payload_usage(tree):
    if not stat.S_ISDIR(tree.lstat().st_mode):
        raise ValueError('Ordinary payload directory required')
    logical = stored = 0
    for path in tree.rglob('*'):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError('Payload links and special files are forbidden')
        logical += path.stat().st_size
        stored += sparse_bytes(path)
    return {'logical_bytes': logical, 'data_extent_bytes': stored}


def master_available(text):
    fields = {}
    for line in text.splitlines():
        name, separator, value = line.partition(':')
        if separator and name in ('Block count', 'Free blocks', 'Reserved block count', 'Block size'):
            if name in fields:
                raise ValueError('Duplicate master capacity field')
            fields[name] = int(value.strip())
    if (len(fields) != 4 or fields['Block size'] != BLOCK or
            not 0 <= fields['Reserved block count'] <= fields['Free blocks'] <= fields['Block count']):
        raise ValueError('Invalid master filesystem capacity')
    available = (fields['Free blocks'] - fields['Reserved block count']) * BLOCK
    if available < MASTER_RESERVE:
        raise ValueError('Master filesystem requires 512MiB free after reserved blocks')
    return available


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--sysroot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new build/ directory")
    parser.add_argument("--config-tree", type=Path, help="shared configuration/tools tree below build/")
    parser.add_argument("--data-mib", type=int, default=DEFAULT_MASTER_MIB)
    parser.add_argument("--disk-bytes", type=int, default=0, help="exact target USB capacity in bytes")
    parser.add_argument("--log-mib", type=int, default=0, help="optional Windows basic-data FAT32 log partition (>=64 MiB)")
    parser.add_argument("--fedora-tree", type=Path, help="verified extracted Live tree below build/")
    args = parser.parse_args()
    if args.log_mib != 0 and args.log_mib < 64:
        parser.error("log partition must be disabled or at least 64 MiB")
    fedora = None
    fedora_mib = 3072 if args.fedora_tree else 0
    if args.fedora_tree:
        fedora = args.fedora_tree.resolve()
        if not fedora.is_relative_to((ROOT / 'build').resolve()) or args.log_mib != 256:
            parser.error('Fedora requires a workspace tree and the fixed 256MiB third log partition')
        fedora_info = json.loads((fedora / 'fedora-manifest.json').read_text())
        if fedora_info['filesystem_uuid'] != 'F044-0107' or fedora_info['payload_bytes'] > 2900 * MIB:
            parser.error('Unexpected Fedora payload')
    config_tree = args.config_tree.resolve() if args.config_tree else None
    if config_tree and (not config_tree.is_relative_to(ROOT / 'build') or not (config_tree / 'usb-config.json').is_file() or args.log_mib != 256):
        parser.error('config-tree must be a prepared build directory with marker and a 256MiB log partition')
    destination = args.output.resolve()
    build = (ROOT / "build").resolve()
    if destination.exists() or destination == build or not destination.is_relative_to(build):
        parser.error("output must be a new directory below build/; block devices are forbidden")
    tree = args.tree.resolve()
    if not tree.is_relative_to(build) or not (tree / "gpu-linux/master.json").is_file():
        parser.error("tree must be a generated appliance tree below build/")
    manifest = json.loads((tree / "gpu-linux/master.json").read_text())
    if manifest.get('shared_registry'):
        if config_tree is None:
            parser.error('shared-registry images require --config-tree')
        marker = json.loads((config_tree / 'usb-config.json').read_text())
        if marker != {'schema': 1, 'kind': 'gpu-linux-config', 'root_version': manifest['version']}:
            parser.error('configuration marker does not match the master version')
    usage = payload_usage(tree)
    if args.data_mib * MIB < usage['data_extent_bytes'] + MASTER_RESERVE:
        parser.error('data image requires payload data extents plus 512MiB reserve; '
                     'filesystems without hole mapping use the full logical size')
    destination.mkdir(parents=True)
    image_sizes = [("efi.img", 64 * MIB), ("usb-data.img", args.data_mib * MIB)]
    if args.log_mib:
        image_sizes.append(("usb-logs.img", args.log_mib * MIB))
    if fedora:
        image_sizes.append(('fedora-live.img', fedora_mib * MIB))
    for name, size in image_sizes:
        with (destination / name).open("xb") as stream:
            stream.truncate(size)
    command = ["bwrap", "--unshare-all", "--uid", "0", "--gid", "0", "--die-with-parent",
               "--ro-bind", str(args.sysroot.resolve()), "/", "--ro-bind", str(tree), "/tree",
               "--bind", str(destination), "/out", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--tmpfs", "/run",
               "--clearenv", "--setenv", "PATH", "/usr/sbin:/usr/bin:/sbin:/bin", "--"]
    if fedora:
        command = command[:-1] + ['--ro-bind', str(fedora), '/run/fedora', '--']
    if config_tree:
        command = command[:-1] + ['--ro-bind', str(config_tree), '/work', '--']
    def run(*values, **kwargs):
        return subprocess.run(command + list(values), check=True, **kwargs)
    run("grub-script-check", "/tree/boot/grub/grub.cfg")
    run("mkfs.fat", "-F", "32", "-n", "GPU-EFI", "/out/efi.img")
    run("mcopy", "-i", "/out/efi.img", "-s", "/tree/EFI", "::/")
    run("mke2fs", "-q", "-t", "ext4", "-b", str(BLOCK), "-F", "-L", "GPU-MASTER", "-U", manifest["usb_data_uuid"],
        "-d", "/tree", "/out/usb-data.img")
    run("e2fsck", "-f", "-n", "/out/usb-data.img")
    available = master_available(run('dumpe2fs', '-h', '/out/usb-data.img',
                                    capture_output=True, text=True).stdout)
    (destination / 'master-capacity.json').write_text(json.dumps({
        **usage, 'partition_bytes': args.data_mib * MIB,
        'available_bytes': available, 'required_free_bytes': MASTER_RESERVE,
    }, indent=2) + '\n')
    if args.log_mib:
        run("mkfs.fat", "-F", "32", "-n", "GPU-LOGS", "/out/usb-logs.img")
        certificate = tree / 'EFI/BOOT/signing-cert.der'
        if certificate.is_file():
            fingerprint = hashlib.sha256(certificate.read_bytes()).hexdigest()
            (destination / 'signing-cert-info.txt').write_text(
                'Public module-signing certificate only. Never copy a private key here.\n'
                'SHA-256 DER fingerprint: ' + fingerprint + '\n')
            run('mcopy', '-i', '/out/usb-logs.img', '/tree/EFI/BOOT/signing-cert.der', '::/signing-cert.der')
            run('mcopy', '-i', '/out/usb-logs.img', '/out/signing-cert-info.txt', '::/signing-cert-info.txt')
        if config_tree:
            run('mcopy', '-i', '/out/usb-logs.img', '-s',
                *['/work/' + path.name for path in sorted(config_tree.iterdir())], '::/')
        log_uuid = run("blkid", "-p", "-s", "UUID", "-o", "value", "/out/usb-logs.img",
                       capture_output=True, text=True).stdout.strip()
        (destination / "log-volume.json").write_text(json.dumps({"uuid": log_uuid, "label": "GPU-LOGS",
             "size": args.log_mib * MIB, "purpose": "manual diagnostic log export; Windows basic data"}, indent=2) + "\n")
    if fedora:
        run('mkfs.fat', '-F', '32', '-i', fedora_info['fat_id'], '-n', 'GPU-FEDORA', '/out/fedora-live.img')
        run('mcopy', '-i', '/out/fedora-live.img', '-s',
            *['/run/fedora/' + path.name for path in sorted(fedora.iterdir())], '::/')
        run('fsck.fat', '-n', '/out/fedora-live.img')
    efi_start, efi_sectors = 2048, 64 * MIB // 512
    data_start = efi_start + efi_sectors
    data_sectors = args.data_mib * MIB // 512
    disk = destination / "gpu-linux-usb.img"
    log_start = data_start + data_sectors
    log_sectors = args.log_mib * MIB // 512
    fedora_start = log_start + log_sectors
    fedora_sectors = fedora_mib * MIB // 512
    minimum_bytes = (fedora_start + fedora_sectors + 2048) * 512
    disk_bytes = args.disk_bytes or minimum_bytes
    if disk_bytes < minimum_bytes or disk_bytes % 512:
        parser.error("disk capacity must be sector aligned and contain both partitions plus GPT backup")
    with disk.open("xb") as stream:
        stream.truncate(disk_bytes)
    layout = ("label: gpt\nunit: sectors\nfirst-lba: 2048\n\n"
              f'start={efi_start},size={efi_sectors},type=U,name="GPU Linux EFI"\n'
              f'start={data_start},size={data_sectors},type=L,name="GPU Linux Master"\n')
    if args.log_mib:
        layout += (f'start={log_start},size={log_sectors},'
                   'type=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7,name="GPU Linux Logs"\n')
    if fedora:
        layout += (f'start={fedora_start},size={fedora_sectors},'
                   'type=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7,name="Fedora Live"\n')
    run("sfdisk", "--no-reread", "--no-tell-kernel", "/out/gpu-linux-usb.img", input=layout, text=True)
    with disk.open("r+b") as target:
        regions = [("efi.img", efi_start), ("usb-data.img", data_start)]
        if args.log_mib:
            regions.append(("usb-logs.img", log_start))
        if fedora:
            regions.append(('fedora-live.img', fedora_start))
        for name, start in regions:
            target.seek(start * 512)
            with (destination / name).open("rb") as source:
                while block := source.read(8 * MIB):
                    target.write(block)
        target.flush()
        os.fsync(target.fileno())
    run("sfdisk", "--verify", "/out/gpu-linux-usb.img")
    table = run("sfdisk", "--json", "/out/gpu-linux-usb.img", capture_output=True, text=True).stdout
    (destination / "partition-table.json").write_text(table)
    with disk.open("rb") as source:
        sha = hashlib.file_digest(source, "sha256").hexdigest()
    (destination / "SHA256SUMS").write_text(sha + "  gpu-linux-usb.img\n")
    print(f"Created file only: {disk}; {disk.stat().st_size} bytes; SHA256={sha}")
    print("Not written to USB. Native boot qualification remains.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f"USB image assembly failed: {error}")
