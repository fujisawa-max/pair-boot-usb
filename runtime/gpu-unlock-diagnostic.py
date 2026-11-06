#!/usr/bin/env python3
"""One explicit pre-NVIDIA unlock attempt, with logs on the USB log partition."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import threading
import time

STATE = Path('/run/gpu-diagnostic')


def preconditions(profile, boot_id, hardware, bound, nvidia_loaded, targets, attempted, cold):
    if not cold or attempted or bound or nvidia_loaded or targets not in ('', '(null)'):
        raise ValueError('Requires a fresh cold baseline boot, no NVIDIA driver, empty targets and no prior attempt')
    if profile.get('hardware_uuid', '').lower() != hardware.strip().lower():
        raise ValueError('Host inventory does not match this PC')
    target = profile.get('gpu_bdf', '')
    if not re.fullmatch(r'[a-f0-9]{4}:[a-f0-9]{2}:[a-f0-9]{2}\.[0-7]', target) or not boot_id:
        raise ValueError('Explicit inventory GPU PCI address and boot ID required')
    return target


def block_parent(device):
    info = os.stat(device)
    if not stat.S_ISBLK(info.st_mode):
        raise ValueError('Expected a block device')
    path = Path(f'/sys/dev/block/{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}').resolve()
    if not (path / 'partition').is_file():
        raise ValueError('Expected a partition, not a whole disk')
    return path.parent, int((path / 'partition').read_text()), int((path / 'size').read_text()) * 512


def save(path, value):
    with path.open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def attempt(log_uuid, cold):
    spec = importlib.util.spec_from_file_location('diag', '/usr/lib/gpu-diagnostic.py')
    diag = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diag)
    diag.guard(diag.read('/proc/cmdline'), Path('/etc/initrd-release').exists())
    if not (STATE / 'master-verified.json').is_file():
        raise ValueError('Run the baseline collector and verify the master first')
    profile = json.loads((STATE / 'host-profile.json').read_text())
    boot_id = diag.read('/proc/sys/kernel/random/boot_id')
    gpu = Path('/sys/bus/pci/devices') / profile.get('gpu_bdf', 'invalid')
    target = preconditions(profile, boot_id, diag.read('/sys/class/dmi/id/product_uuid'),
                           (gpu / 'driver').exists(), Path('/sys/module/nvidia').exists(),
                           diag.read('/sys/module/gpu_unlock/parameters/targets'),
                           (STATE / 'manual-unlock-attempt.json').exists(), cold)
    if diag.read(gpu / 'vendor') != '0x10de' or diag.read(gpu / 'device') != '0x2504':
        raise ValueError('This diagnostic attempt is restricted to the reviewed RTX3060 10de:2504')
    if not re.fullmatch(r'[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}', log_uuid):
        raise ValueError('Explicit USB FAT log UUID required')
    storage = diag.storage_module()
    config = storage.cmdline(diag.read('/proc/cmdline'))
    master_device = storage.device(config.get('gpu_root.usb_uuid', ''))
    logs_device = storage.device(log_uuid)
    master_parent, master_part, _ = block_parent(master_device)
    log_parent, log_part, size = block_parent(logs_device)
    if master_parent != log_parent or master_part != 2 or log_part != 3 or size != 256 * 1024 * 1024:
        raise ValueError('Logs must be partition 3 on the same USB as the master; EFI/Windows are forbidden')
    for field, expected in (('TYPE', 'vfat'), ('LABEL', 'GPU-LOGS')):
        value = subprocess.check_output(['blkid', '-p', '-s', field, '-o', 'value', logs_device], text=True).strip()
        if value != expected:
            raise ValueError('USB log filesystem identity mismatch')
    if subprocess.run(['findmnt', '-n', '-S', logs_device], stdout=subprocess.DEVNULL).returncode == 0:
        raise ValueError('USB log partition is already mounted; unmount it before this attempt')
    mount = STATE / 'unlock-logs'
    mount.mkdir(exist_ok=True)
    subprocess.run(['mount', '-t', 'vfat', '-o', 'rw,nodev,nosuid,noexec', logs_device, str(mount)], check=True)
    logger = None
    thread = None
    directory = None
    created = False
    report = {'boot_id': boot_id, 'target': target, 'status': 'preparing', 'method': 'manual before NVIDIA'}
    try:
        if 'rw' not in diag.mount_options(mount):
            raise ValueError('USB log mount is not writable')
        directory = mount / ('GPU-UNLOCK-' + boot_id[:8])
        directory.mkdir(exist_ok=False)
        created = True
        for source in STATE.iterdir():
            if source.is_file() and not source.is_symlink() and source.suffix in ('.txt', '.json'):
                shutil.copy2(source, directory / source.name)
        save(STATE / 'manual-unlock-attempt.json', report)
        save(directory / 'attempt.json', report)
        subprocess.run(['sync', '-f', str(mount)], check=True)
        ready = threading.Event()
        marker = 'GPU-DIAG-UNLOCK LOGGER READY ' + boot_id
        logfile = (directory / 'kernel-live.txt').open('w', buffering=1)
        logger = subprocess.Popen(['dmesg', '--follow-new', '--decode'], stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, errors='replace')
        def follow():
            for line in logger.stdout:
                logfile.write(line)
                logfile.flush()
                os.fsync(logfile.fileno())
                if marker in line:
                    ready.set()
            logfile.close()
        thread = threading.Thread(target=follow, daemon=True)
        thread.start()
        time.sleep(0.2)
        with open('/dev/kmsg', 'w') as stream:
            stream.write('<6>' + marker + '\n')
        if not ready.wait(10):
            raise ValueError('Durable kernel logger is not ready; no unlock attempted')
        print('USB basic logs saved. One manual unlock attempt begins now. Film the console.', flush=True)
        with (directory / 'modprobe.txt').open('w') as output:
            subprocess.run(['modprobe', '-r', 'gpu_unlock'], stdout=output, stderr=subprocess.STDOUT, check=True, timeout=10)
            report['status'] = 'launching'
            save(directory / 'attempt.json', report)
            subprocess.run(['sync', '-f', str(mount)], check=True)
            if not thread.is_alive():
                raise ValueError('Kernel logger stopped; no unlock attempted')
            proc = subprocess.Popen(['modprobe', 'gpu_unlock', 'targets=' + target], stdout=output, stderr=subprocess.STDOUT)
            code = proc.wait(timeout=60)
        time.sleep(0.5)
        kernel = subprocess.check_output(['dmesg', '--decode'], text=True, errors='replace')
        (directory / 'kernel-after-attempt.txt').write_text(kernel)
        save(STATE / 'manual-unlock-attempt.json', {**report, 'modprobe_exit': code})
        recent = kernel.split(marker, 1)[-1] if marker in kernel else ''
        success = code == 0 and any(target in line.lower() and re.search(r'gpu_unlock: unlocked\s*$', line)
                                    for line in recent.splitlines())
        report.update(status='unlocked' if success else 'failed', modprobe_exit=code)
        save(STATE / 'manual-unlock.json', report)
        save(directory / 'attempt.json', report)
        print(json.dumps(report, indent=2), flush=True)
        if success:
            print('Next optional command: gpu-diag-nvidia --after-manual-unlock', flush=True)
        else:
            print('No NVIDIA load or retry. Keep the failure logs; power off fully before another boot.', flush=True)
    except Exception as error:
        report.update(status='failed', error=str(error))
        save(STATE / 'manual-unlock.json', report)
        if created:
            save(directory / 'attempt.json', report)
        raise
    finally:
        if logger is not None:
            logger.terminate()
            try:
                logger.wait(timeout=3)
            except subprocess.TimeoutExpired:
                logger.kill()
                logger.wait()
        if thread is not None:
            thread.join(timeout=3)
        subprocess.run(['sync', '-f', str(mount)], check=False)
        if subprocess.run(['umount', str(mount)]).returncode:
            print('USB log partition still busy; keep USB inserted. No forced unmount.', file=sys.stderr)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log-uuid', required=True)
    parser.add_argument('--cold-boot-confirmed', action='store_true', required=True)
    args = parser.parse_args()
    try:
        attempt(args.log_uuid, args.cold_boot_confirmed)
    except Exception as error:
        sys.exit('MANUAL GPU UNLOCK STOP: ' + str(error))
