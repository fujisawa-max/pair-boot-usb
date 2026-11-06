#!/usr/bin/env python3
"""Portable fleet registry format, validation, conflict detection and file deployment."""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import uuid

MAX_BYTES = 1024 * 1024
# Generated from config/rtx30-gpu-policy.json; fixture checks prevent drift.
GPU_NORMAL_IDS = frozenset('10de:2203 10de:2204 10de:2206 10de:2207 10de:2208 10de:220a 10de:2216 10de:2414 10de:2420 10de:2460 10de:2482 10de:2484 10de:2486 10de:2487 10de:2488 10de:2489 10de:249c 10de:249d 10de:24a0 10de:24c7 10de:24c9 10de:24dc 10de:24dd 10de:24e0 10de:2503 10de:2504 10de:2507 10de:2508 10de:2520 10de:2521 10de:2523 10de:2544 10de:2560 10de:2563 10de:2582 10de:2584 10de:25a0 10de:25a2 10de:25a5 10de:25ab 10de:25ac 10de:25e0 10de:25e2 10de:25e5 10de:25ec 10de:2822 10de:28a3 10de:28e3'.split())
GPU_UNLOCK_IDS = frozenset('10de:2203 10de:2204 10de:2206 10de:2207 10de:2208 10de:220a 10de:2216 10de:2414 10de:2482 10de:2484 10de:2486 10de:2487 10de:2488 10de:2489 10de:249c 10de:249d 10de:24a0 10de:24c7 10de:24c9 10de:24dc 10de:24dd 10de:24e0 10de:2503 10de:2504 10de:2507 10de:2508 10de:2544'.split())


def validate_storage_directory(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9._-]{0,63}', value) or value.endswith('.'):
        raise ValueError('Storage directory must be one safe ASCII folder name (1-64 characters)')
    stem = value.split('.', 1)[0].lower()
    if stem in {'con', 'prn', 'aux', 'nul', 'windows', 'users', 'programdata', 'boot', 'recovery', 'efi', 'perflogs', 'msocache'} or re.fullmatch(r'(com|lpt)[1-9]', stem):
        raise ValueError('Reserved Windows/device directory name')
    return value


def hardware_uuid(value):
    if not isinstance(value, str):
        raise ValueError('Invalid hardware UUID')
    parsed = str(uuid.UUID(value))
    if parsed != value.lower() or parsed in ('00000000-0000-0000-0000-000000000000', 'ffffffff-ffff-ffff-ffff-ffffffffffff'):
        raise ValueError('Invalid hardware UUID')
    return parsed


def validate_partition_policy(value, target_key='ntfs_uuid'):
    if value.get('partition_is_system') is not False or value.get('partition_is_hidden') is not False:
        raise ValueError('System/hidden/unknown partition is forbidden')
    boot = value.get('partition_is_boot')
    if type(boot) is not bool:
        raise ValueError('Unknown boot partition flag')
    consent = value.get('personal_os_volume_consent')
    if boot or consent is not None:
        expected = {'policy': 'personal-os-v1',
                    'hardware_uuid': value.get('hardware_uuid'),
                    'target': value.get(target_key),
                    'storage_directory': value.get('storage_directory')}
        if consent != expected:
            raise ValueError('Personal OS volume requires explicit target-bound consent')
    return consent


def validate_usb_storage_target(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid USB storage target metadata')
    if value.get('kind') == 'usb-ntfs-v1':
        fields = {'kind', 'partition_guid', 'disk_guid', 'offset_bytes', 'size_bytes'}
        if set(value) != fields:
            raise ValueError('Invalid GPT USB target metadata')
        for key in ('partition_guid', 'disk_guid'):
            if hardware_uuid(value.get(key)) != value[key]:
                raise ValueError('USB target GUID must be canonical')
    elif value.get('kind') == 'usb-ntfs-mbr-v1':
        fields = {'kind', 'disk_signature', 'partition_number', 'offset_bytes', 'size_bytes'}
        if set(value) != fields or not re.fullmatch(r'[0-9a-f]{8}', str(value.get('disk_signature', ''))) or value['disk_signature'] in ('00000000', 'ffffffff'):
            raise ValueError('Invalid MBR disk signature/metadata')
        if type(value.get('partition_number')) is not int or not 1 <= value['partition_number'] <= 4:
            raise ValueError('MBR requires a primary partition number 1-4')
    else:
        raise ValueError('Unknown USB storage target kind')
    for key in ('offset_bytes', 'size_bytes'):
        if type(value.get(key)) is not int or value[key] <= 0 or value[key] % 512:
            raise ValueError('Invalid USB partition geometry')
    return dict(value)


def record(value, now=None):
    if not isinstance(value, dict) or value.get('schema') != 1:
        raise ValueError('Invalid host schema')
    host = hardware_uuid(value.get('hardware_uuid'))
    if not re.fullmatch(r'[A-Fa-f0-9]{16}', str(value.get('ntfs_uuid', ''))):
        raise ValueError('Invalid NTFS UUID')
    if not re.fullmatch(r'[a-f0-9]{4}:[a-f0-9]{2}:[a-f0-9]{2}\.[0-7]', str(value.get('gpu_bdf', ''))):
        raise ValueError('Explicit GPU PCI BDF required; register from Fedora')
    if value.get('gpu_pci_id') not in GPU_NORMAL_IDS:
        raise ValueError('GPU is not in this release RTX30 normal-mode PCI policy')
    checked = value.get('checked_at_unix')
    if type(checked) not in (int, float) or not math.isfinite(checked):
        raise ValueError('Invalid Windows inventory timestamp')
    age = (time.time() if now is None else now) - checked
    if age < 0 or age > 30 * 86400:
        raise ValueError('Windows inventory expired/future dated; refresh it')
    if value.get('bitlocker_volume_status') != 'FullyDecrypted' or value.get('bitlocker_protection') != 'Off':
        raise ValueError('BitLocker encrypted/protected/unknown')
    if value.get('fast_startup_enabled') is not False or value.get('secure_boot_enabled') is not True:
        raise ValueError('Fast Startup must be off and Secure Boot explicitly checked')
    consent = validate_partition_policy(value)
    usb_target = None
    if 'storage_target' in value:
        usb_target = validate_usb_storage_target(value['storage_target'])
        if value.get('partition_is_boot') is not False or consent is not None:
            raise ValueError('USB root target must be a data partition without OS exception')
    validate_storage_directory(value.get('storage_directory'))
    allowed = ('schema', 'hardware_uuid', 'ntfs_uuid', 'gpu_bdf', 'gpu_pci_id', 'checked_at_unix',
               'bitlocker_volume_status', 'bitlocker_protection', 'fast_startup_enabled',
               'secure_boot_enabled', 'partition_is_boot', 'partition_is_system',
               'partition_is_hidden', 'storage_directory')
    result = {key: value[key] for key in allowed}
    if consent is not None:
        result['personal_os_volume_consent'] = dict(consent)
    if usb_target is not None:
        result['storage_target'] = usb_target
    result['hardware_uuid'] = host
    result['ntfs_uuid'] = result['ntfs_uuid'].upper()
    return result


def encode(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return (json.dumps({'schema': 1, 'payload_base64': base64.b64encode(raw).decode(),
                        'sha256': hashlib.sha256(raw).hexdigest()}, indent=2) + '\n').encode()


def decode(data, now=None):
    if len(data) > MAX_BYTES:
        raise ValueError('Registry too large')
    envelope = json.loads(data)
    if envelope.get('schema') != 1:
        raise ValueError('Unknown registry envelope')
    raw = base64.b64decode(envelope['payload_base64'], validate=True)
    if hashlib.sha256(raw).hexdigest() != envelope.get('sha256'):
        raise ValueError('Registry checksum mismatch')
    value = json.loads(raw)
    if value.get('schema') != 1 or type(value.get('generation')) is not int or value['generation'] < 1:
        raise ValueError('Invalid registry generation')
    if not re.fullmatch(r'[a-zA-Z0-9._-]{1,80}', value.get('root_version', '')):
        raise ValueError('Invalid root version')
    hosts = value.get('records')
    if not isinstance(hosts, list) or len(hosts) > 1000:
        raise ValueError('Invalid records list')
    cleaned = [record(item, now) for item in hosts]
    if len({item['hardware_uuid'] for item in cleaned}) != len(cleaned):
        raise ValueError('Duplicate host records')
    return {**value, 'records': cleaned}


def load(path, now=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Expected bounded regular registry file')
    return decode(path.read_bytes(), now)


def for_version(registry, version, now=None):
    if registry['root_version'] == version:
        return decode(encode(registry), now)
    if registry['root_version'] != '0.7.8-shared' or version != '0.8.0-shared':
        raise ValueError('Unsupported registry version migration')
    return merge([registry], registry['generation'] + 1, version, now)


def merge(inputs, generation, root_version, now=None):
    hosts = {}
    prior = 0
    for value in inputs:
        if 'records' in value:
            if value['root_version'] != root_version and not (value['root_version'] == '0.7.8-shared' and root_version == '0.8.0-shared'):
                raise ValueError('Registry root versions conflict')
            prior = max(prior, value['generation'])
            values = value['records']
        else:
            values = [value]
        for raw in values:
            item = record(raw, now)
            host = item['hardware_uuid']
            if host in hosts:
                # Only refresh time may differ. Mapping/safety conflicts need owner resolution.
                left = {k: v for k, v in hosts[host].items() if k != 'checked_at_unix'}
                right = {k: v for k, v in item.items() if k != 'checked_at_unix'}
                if left != right:
                    raise ValueError('Conflicting configuration for host ' + host)
                item['checked_at_unix'] = max(item['checked_at_unix'], hosts[host]['checked_at_unix'])
            hosts[host] = item
    if type(generation) is not int or generation <= prior:
        raise ValueError('New generation must exceed all input generations')
    value = {'schema': 1, 'generation': generation, 'root_version': root_version,
             'records': [hosts[key] for key in sorted(hosts)]}
    return decode(encode(value), now)


def atomic_write(path, data):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Refusing symlink target')
    temporary = path.with_name('.' + path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if temporary.read_bytes() != data:
            raise ValueError('Write readback mismatch')
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def environment(bdf, unlock=True, default_normal=False):
    if not re.fullmatch(r'[a-f0-9]{4}:[a-f0-9]{2}:[a-f0-9]{2}\.[0-7]', bdf):
        raise ValueError('Invalid BDF')
    raw = ('# GRUB Environment Block\ngpu_targets=' + (bdf if unlock else 'unsupported') +
           '\ngpu_default=' + ('0' if default_normal else '1') + '\n').encode('ascii')
    return raw + b'#' * (1024 - len(raw))


def deploy(registry, mount, now=None):
    mount = Path(mount)
    if mount.is_symlink() or not mount.is_dir():
        raise ValueError('Explicit configuration directory required')
    marker_path = mount / 'usb-config.json'
    if marker_path.is_symlink():
        raise ValueError('Unsafe USB marker')
    marker = json.loads(marker_path.read_text())
    value = decode(encode(registry), now)
    if marker != {'schema': 1, 'kind': 'gpu-linux-config', 'root_version': value['root_version']}:
        raise ValueError('USB configuration marker/version mismatch')
    config = mount / 'gpu-config'
    config.mkdir(exist_ok=True)
    if config.is_symlink():
        raise ValueError('Unsafe config directory')
    current = config / 'registry.json'
    if current.exists():
        old = load(current, now)
        if old['generation'] > value['generation']:
            raise ValueError('Refusing registry rollback')
        if old['generation'] == value['generation'] and old != value:
            raise ValueError('Same generation has different contents')
        # Deployment must never silently remove a host or change its destination.
        prior = {v['hardware_uuid']: v for v in old['records']}
        new = {v['hardware_uuid']: v for v in value['records']}
        for key, item in prior.items():
            if key not in new or any(item[k] != new[key][k] for k in item if k != 'checked_at_unix'):
                raise ValueError('Deployment would remove/change a registered host')
    hosts = config / 'hosts'
    hosts.mkdir(exist_ok=True)
    if hosts.is_symlink():
        raise ValueError('Unsafe host environment directory')
    for item in value['records']:
        atomic_write(hosts / (item['hardware_uuid'] + '.env'), environment(
            item['gpu_bdf'], item['gpu_pci_id'] in GPU_UNLOCK_IDS, item['gpu_pci_id'] != '10de:2504'))
    # Commit registry last. Partial environment updates cannot bypass runtime matching.
    atomic_write(current, encode(value))
