#!/usr/bin/env python3
"""State image provisioning and early identity setup, only in managed initramfs."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

SCHEMA = 1
BINDINGS = (('home/gpu', 'home/gpu'), ('system/gpu-root', 'var/lib/gpu-root'),
            ('system/machine-id', 'etc/machine-id'), ('system/hostname', 'etc/hostname'),
            ('cache/gpu-inference', 'var/cache/gpu-inference'))


def metadata(api, manifest):
    value = manifest.get('persistent_state')
    if not isinstance(value, dict) or value.get('schema') != SCHEMA or value.get('filename') != 'state.img':
        raise api.SafetyError('Unsupported persistent state layout')
    if (type(value.get('size')) is not int or value['size'] < 64 * 1024**2
            or not re.fullmatch(r'[a-f0-9]{64}', value.get('sha256', ''))):
        raise api.SafetyError('Invalid persistent state template')
    return value


def inspect(api, parent, hardware):
    target = api.safe_child(parent, 'state.img')
    receipt = api.safe_child(parent, 'state-image.json')
    for name in ('.state-copy-in-progress', '.state.img.partial', '.state-maintenance.json',
                 '.state-maintenance.img', '.state-maintenance-receipt.json'):
        if api.safe_child(parent, name).exists():
            raise api.SafetyError('Interrupted state transaction; preserve files and inspect from Fedora')
    api.regular(target)
    api.regular(receipt)
    info = json.loads(receipt.read_text())
    if (info.get('schema') != 1 or info.get('state_schema') != SCHEMA or info.get('version') != 'state-v1'
            or info.get('hardware_sha256') != hashlib.sha256(hardware.strip().lower().encode()).hexdigest()
            or info.get('size') != target.stat().st_size
            or not re.fullmatch(r'[a-f0-9]{64}', info.get('original_sha256', ''))):
        raise api.SafetyError('State receipt/PC/schema mismatch; never reinitialize existing state')
    if api.run('losetup', '-j', str(target)):
        raise api.SafetyError('State image is already attached')
    return target, info


def create(api, master, parent, value, hardware, free_bytes=None):
    target = api.safe_child(parent, 'state.img')
    receipt = api.safe_child(parent, 'state-image.json')
    marker = api.safe_child(parent, '.state-copy-in-progress')
    temporary = api.safe_child(parent, '.state.img.partial')
    for path in (target, receipt, marker, temporary):
        if path.exists():
            raise api.SafetyError('State output exists; no automatic replacement or retry')
    api.regular(master)
    if master.stat().st_size != value['size'] or api.digest(master) != value['sha256']:
        raise api.SafetyError('State master size/hash mismatch')
    api.validate_ext4(master)
    available = shutil.disk_usage(parent).free if free_bytes is None else free_bytes
    if available < value['size'] + 1024**3:
        raise api.SafetyError('Insufficient storage for state and reserve')
    api.new_json(marker, {'schema': 1, 'state': 'copying'})
    with master.open('rb') as source, temporary.open('xb') as output:
        shutil.copyfileobj(source, output, 8 * 1024**2)
        output.flush()
        os.fsync(output.fileno())
    if temporary.stat().st_size != value['size'] or api.digest(temporary) != value['sha256']:
        raise api.SafetyError('State copy verification failed; transaction retained')
    api.atomic_new(temporary, target)
    api.sync_directory(parent)
    api.new_json(receipt, {'schema': 1, 'state_schema': SCHEMA, 'version': 'state-v1',
                          'size': value['size'], 'original_sha256': value['sha256'],
                          'hardware_sha256': hashlib.sha256(hardware.strip().lower().encode()).hexdigest()})
    marker.unlink()
    api.sync_directory(parent)
    return target


def initialize(api, mounted, hardware):
    """Initialize ordinary UUID data before PID1; never generate signing/SSH keys."""
    for relative in ('home/gpu', 'system', 'system/gpu-root', 'cache/gpu-inference'):
        path = mounted / relative
        if path.is_symlink() or not path.is_dir():
            raise api.SafetyError('State directory missing or linked: ' + relative)
    layout = mounted / 'layout.json'
    api.regular(layout)
    if json.loads(layout.read_text()) != {'schema': SCHEMA, 'kind': 'pair-boot-state'}:
        raise api.SafetyError('State filesystem layout mismatch')
    marker = mounted / 'system/gpu-root/identity.json'
    machine = mounted / 'system/machine-id'
    hostname = mounted / 'system/hostname'
    for path in (machine, hostname):
        api.regular(path)
    fingerprint = hashlib.sha256(hardware.strip().lower().encode()).hexdigest()
    if marker.exists() or marker.is_symlink():
        api.regular(marker)
        info = json.loads(marker.read_text())
        if (info.get('hardware_sha256') != fingerprint
                or not re.fullmatch(r'[a-f0-9]{32}', info.get('machine_id', ''))
                or info['machine_id'] != machine.read_text().strip()
                or info.get('hostname') != 'gpu-' + info['machine_id'][:12]
                or hostname.read_text().strip() != info['hostname']):
            raise api.SafetyError('Persistent OS identity mismatch')
        return info
    # Nonempty identity files without their record are an interrupted initialization.
    if machine.read_text().strip() or hostname.read_text().strip():
        raise api.SafetyError('Incomplete state identity; no regeneration')
    machine_id = uuid.uuid4().hex
    info = {'hardware_sha256': fingerprint, 'machine_id': machine_id, 'hostname': 'gpu-' + machine_id[:12]}
    for path, value in ((machine, machine_id), (hostname, info['hostname'])):
        with path.open('w') as stream:
            stream.write(value + '\n')
            stream.flush()
            os.fsync(stream.fileno())
    api.new_json(marker, info)
    api.sync_directory(machine.parent)
    return info


def attach(api, image, hardware, sysroot, mounted_paths):
    """Caller records each mount immediately so any failure can unwind it."""
    loop = api.run('losetup', '--find', '--show', '--nooverlap', str(image))
    try:
        mountpoint = sysroot / 'persist'
        api.run('mount', '-t', 'ext4', '-o', 'rw,nodev,nosuid,noexec', loop, str(mountpoint))
        mounted_paths.append(mountpoint)
        initialize(api, mountpoint, hardware)
        for source, destination in BINDINGS:
            target = sysroot / destination
            if target.is_symlink() or not target.exists():
                raise api.SafetyError('Invalid state bind destination: ' + destination)
            api.run('mount', '--bind', str(mountpoint / source), str(target))
            mounted_paths.append(target)
        return loop
    except BaseException:
        for path in reversed(mounted_paths):
            api.run('umount', str(path))
        mounted_paths.clear()
        api.run('losetup', '-d', loop)
        raise
