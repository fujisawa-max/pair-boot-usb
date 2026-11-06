#!/usr/bin/env python3
"""Collect native boot evidence in RAM. Windows storage is never mounted."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

STATE = Path('/run/gpu-diagnostic')


def guard(text, initrd=True):
    words = text.split()
    for key, value in (('gpu_root.diagnostic', '1'), ('gpu_root.allow_ntfs_write', '0')):
        values = [word.split('=', 1)[1] for word in words if word.startswith(key + '=')]
        if values != [value]:
            raise ValueError(f'Diagnostic requires exactly {key}={value}')
    if not initrd or any(word.startswith('gpu_unlock.targets=') for word in words):
        raise ValueError('Only baseline diagnostic initramfs is allowed')


def save(name, value):
    (STATE / name).write_text(value, encoding='utf-8')


def capture(name, args, timeout=45):
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors='replace', timeout=timeout)
        code, output = result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired) as error:
        code, output = -1, str(error)
    save(name + '.txt', output + f'\n[exit={code}]\n')
    return code, output


def read(path):
    try:
        return Path(path).read_text(errors='replace').strip()
    except OSError as error:
        return f'unavailable: {error}'


def storage_module():
    spec = importlib.util.spec_from_file_location('storage', '/usr/lib/gpu-root.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mount_options(path):
    result = subprocess.run(['findmnt', '-n', '-M', str(path), '-o', 'OPTIONS'],
                            capture_output=True, text=True)
    return set(result.stdout.strip().split(',')) if result.returncode == 0 else set()


def mount_readonly(source, target, loop=False):
    target.mkdir(parents=True, exist_ok=True)
    options = 'ro,noload,nodev,nosuid' + (',loop' if loop else ',noexec')
    subprocess.run(['mount', '-t', 'ext4', '-o', options, str(source), str(target)], check=True)
    if 'ro' not in mount_options(target):
        raise ValueError('Read-only mount verification failed')


def ntfs_checks(storage, device):
    kind = subprocess.check_output(['blkid', '-p', '-s', 'TYPE', '-o', 'value', device], text=True).strip()
    storage.check_volume(kind, 0)  # Refuse BitLocker before running NTFS tools.
    if subprocess.run(['findmnt', '-n', '-S', device], stdout=subprocess.DEVNULL).returncode == 0:
        raise ValueError('NTFS already mounted; diagnostic probes skipped')
    code, _ = capture('ntfs-readonly-probe', ['ntfs-3g.probe', '--readonly', device])
    capture('ntfs-volume-info', ['ntfsinfo', '-m', device])
    if code != 0:
        raise ValueError(f'NTFS read-only probe failed: {code}')
    return 'NTFS read-only probe=0; writable safety/hibernation NOT established'


def collect():
    summary = ['Baseline only; Windows NTFS is NOT mounted; reports are in RAM.']
    save('cmdline.txt', read('/proc/cmdline'))
    for name, args in (
        ('blocks', ['lsblk', '-o', 'NAME,TRAN,SIZE,TYPE,FSTYPE,UUID,MOUNTPOINTS']),
        ('blkid', ['blkid']), ('mounts', ['findmnt']),
        ('modules', ['cat', '/proc/modules']), ('inputs', ['cat', '/proc/bus/input/devices']),
        ('kernel', ['journalctl', '-k', '-b', '--no-pager', '-o', 'short-monotonic']),
        ('journal', ['journalctl', '-b', '--no-pager', '-o', 'short-monotonic']),
        ('gpu-unlock-signature', ['modinfo', '-F', 'signer', 'gpu_unlock']),
        ('udev-status', ['systemctl', 'status', 'systemd-udevd.service', '--no-pager']),
    ):
        capture(name, args)
    hardware = {'kernel': os.uname().release, 'cpu': read('/proc/cpuinfo'),
                'lockdown': read('/sys/kernel/security/lockdown'), 'pci': [], 'dmi': {}, 'secure_boot': {}}
    for name in ('product_uuid', 'product_name', 'board_vendor', 'board_name', 'bios_vendor', 'bios_version', 'bios_date'):
        hardware['dmi'][name] = read('/sys/class/dmi/id/' + name)
    for name in ('SecureBoot', 'SetupMode'):
        paths = list(Path('/sys/firmware/efi/efivars').glob(name + '-*'))
        try:
            hardware['secure_boot'][name] = paths[0].read_bytes()[4] if len(paths) == 1 else None
        except OSError:
            hardware['secure_boot'][name] = None
    for device in sorted(Path('/sys/bus/pci/devices').glob('*')):
        row = {'bdf': device.name}
        for field in ('vendor', 'device', 'subsystem_vendor', 'subsystem_device', 'revision', 'class', 'irq',
                      'current_link_speed', 'current_link_width', 'max_link_speed', 'max_link_width'):
            row[field] = read(device / field)
        row['driver'] = os.path.realpath(device / 'driver') if (device / 'driver').exists() else None
        hardware['pci'].append(row)
    save('hardware.json', json.dumps(hardware, indent=2))
    summary += [f'Kernel: {hardware["kernel"]}', f'DMI UUID: {hardware["dmi"]["product_uuid"]}',
                f'Secure Boot: {hardware["secure_boot"]}', f'Lockdown: {hardware["lockdown"]}']
    for gpu in hardware['pci']:
        if gpu['vendor'] == '0x10de':
            summary.append(f'NVIDIA {gpu["bdf"]}: {gpu["device"]} subsystem {gpu["subsystem_vendor"]}:{gpu["subsystem_device"]} driver={gpu["driver"]}')
    storage = storage_module()
    config = storage.cmdline(read('/proc/cmdline'))
    try:
        usb = storage.device(config.get('gpu_root.usb_uuid', ''))
        kind = subprocess.check_output(['blkid', '-p', '-s', 'TYPE', '-o', 'value', usb], text=True).strip()
        if kind != 'ext4':
            raise ValueError('USB master must be ext4')
        usb_mount = STATE / 'usb'
        mount_readonly(usb, usb_mount)
        summary.append(f'USB master mounted ro,noload: {usb}')
        manifest = storage.manifest_at(usb_mount / 'gpu-linux/master.json')
        save('master-manifest.json', json.dumps(manifest, indent=2))
        summary.append('Master version: ' + manifest['version'])
        try:
            profile = storage.host_profile(usb_mount, hardware['dmi']['product_uuid'])
            save('host-profile.json', json.dumps(profile, indent=2))
            ntfs = storage.device(profile['ntfs_uuid'])
            summary.append(f'Host inventory OK; selected NTFS: {ntfs} UUID={profile["ntfs_uuid"]}')
            summary.append(ntfs_checks(storage, ntfs))
        except Exception as error:
            summary.append('NTFS PREFLIGHT STOP: ' + str(error))
        master = usb_mount / 'gpu-linux/root.img'
        storage.regular(master)
        if master.stat().st_size != manifest['size']:
            raise ValueError('Master size mismatch')
        save('summary.txt', '\n'.join(summary) + '\n')
        print('\n'.join(summary), flush=True)
        print('Checking 6 GiB master SHA-256; this can take several minutes. Please wait.', flush=True)
        with master.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != manifest['sha256']:
            raise ValueError('Master SHA-256 mismatch')
        summary.append('Master SHA-256: PASS')
        code, _ = capture('master-e2fsck', ['e2fsck', '-f', '-n', str(master)], timeout=180)
        if code != 0:
            raise ValueError('Master ext4 check failed or timed out; optional driver test disabled')
        mount_readonly(master, STATE / 'root', loop=True)
        save('master-verified.json', json.dumps({'sha256': actual, 'version': manifest['version']}))
        summary.append('Master ext4: PASS; read-only root ready for optional NVIDIA baseline test')
    except Exception as error:
        summary.append('MASTER PREFLIGHT STOP: ' + str(error))
    save('summary.txt', '\n'.join(summary) + '\n')
    capture('kernel-final', ['journalctl', '-k', '-b', '--no-pager', '-o', 'short-monotonic'])
    print('\n'.join(summary), flush=True)
    print('Reports: /run/gpu-diagnostic ; optional command: gpu-diag-nvidia ; finish: gpu-diag-finish', flush=True)


def nvidia(after_manual=False):
    root = STATE / 'root'
    if not (STATE / 'master-verified.json').is_file() or 'ro' not in mount_options(root):
        raise ValueError('Verified read-only master root is required')
    targets = read('/sys/module/gpu_unlock/parameters/targets')
    if after_manual:
        receipt = json.loads((STATE / 'manual-unlock.json').read_text())
        if (receipt.get('status') != 'unlocked' or receipt.get('boot_id') != read('/proc/sys/kernel/random/boot_id')
                or receipt.get('target') != targets):
            raise ValueError('Successful manual unlock receipt for this boot and target is required')
    elif targets not in ('', '(null)'):
        raise ValueError('Baseline requires empty targets; manual unlock needs the explicit after-manual-unlock option')
    print('NVIDIA TEST: ' + ('after verified manual unlock' if after_manual else 'baseline; no GPU unlock'), flush=True)
    firmware_path = Path('/sys/module/firmware_class/parameters/path')
    previous = firmware_path.read_text().strip()
    try:
        firmware_path.write_text(str(root / 'usr/lib/firmware'))
        for path in ('dev', 'proc', 'sys'):
            target = root / path
            if not mount_options(target):
                subprocess.run(['mount', '--bind', '/' + path, str(target)], check=True)
        print('Loading signed nvidia.ko from verified master...', flush=True)
        code, output = capture('nvidia-modprobe', ['modprobe', '-d', str(root), 'nvidia'], timeout=60)
        print(output, flush=True)
        if code:
            raise ValueError('NVIDIA module load failed; see nvidia-modprobe.txt')
        capture('nvidia-device-nodes', ['chroot', str(root), '/usr/bin/nvidia-modprobe', '-c', '0'])
        code, output = capture('nvidia-smi', ['chroot', str(root), '/usr/bin/nvidia-smi'], timeout=60)
        print(output, flush=True)
        if code == 0:
            capture('nvidia-smi-query', ['chroot', str(root), '/usr/bin/nvidia-smi', '-q'], timeout=60)
    finally:
        firmware_path.write_text(previous)
        capture('kernel-after-nvidia', ['journalctl', '-k', '-b', '--no-pager', '-o', 'short-monotonic'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('collect', 'nvidia'))
    parser.add_argument('--after-manual-unlock', action='store_true')
    args = parser.parse_args()
    guard(read('/proc/cmdline'), Path('/etc/initrd-release').exists())
    STATE.mkdir(mode=0o700, exist_ok=True)
    if args.action == 'collect':
        collect()
    else:
        nvidia(args.after_manual_unlock)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        sys.exit(f'GPU DIAGNOSTIC STOP: {error}')
