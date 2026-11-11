#!/usr/bin/env python3
"""Manage fleet registry files; register only against this Fedora USB's hardware."""
import argparse
import hashlib
import importlib.util
import json
import re
import time
import math
from pathlib import Path
import subprocess
import sys

spec = importlib.util.spec_from_file_location('fleet_registry', Path(__file__).with_name('fleet-registry.py'))
if not Path(spec.origin).exists():
    spec = importlib.util.spec_from_file_location('fleet_registry', Path(__file__).resolve().parents[1] / 'runtime/fleet-registry.py')
fleet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fleet)

storage_path = Path(__file__).with_name('storage-target.py')
if not storage_path.exists():
    storage_path = Path(__file__).resolve().parents[1] / 'runtime/storage-target.py'
storage_spec = importlib.util.spec_from_file_location('storage_target', storage_path)
storage = importlib.util.module_from_spec(storage_spec)
storage_spec.loader.exec_module(storage)


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def live_config(mount):
    mount = mount.resolve()
    source = run('findmnt', '-n', '-o', 'SOURCE', '--target', str(mount))
    live = run('findmnt', '-n', '-o', 'SOURCE', '--target', '/run/initramfs/live')
    if not source.startswith('/dev/') or not live.startswith('/dev/'):
        raise ValueError('Explicit Live USB and configuration block devices required')
    source, live = str(Path(source).resolve()), str(Path(live).resolve())
    parent = run('lsblk', '-dn', '-o', 'PKNAME', source)
    if not parent or parent != run('lsblk', '-dn', '-o', 'PKNAME', live):
        raise ValueError('Configuration is not on the same USB as this Fedora Live')
    if run('lsblk', '-dn', '-o', 'TRAN', '/dev/' + parent) != 'usb':
        raise ValueError('Expected USB transport')
    if run('lsblk', '-dn', '-o', 'LABEL', source) != 'GPU-LOGS' or run('lsblk', '-dn', '-o', 'FSTYPE', source) != 'vfat':
        raise ValueError('Expected GPU-LOGS FAT partition')
    if run('findmnt', '-n', '-o', 'TARGET', '--target', str(mount)) != str(mount):
        raise ValueError('Use the configuration partition mountpoint itself')
    return source


def read_input(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > fleet.MAX_BYTES:
        raise ValueError('Expected bounded regular inventory/evidence file')
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    return fleet.load(path) if 'payload_base64' in raw else raw


def mbr_candidate_target(value):
    identity = value.get('partition_identity')
    if not isinstance(identity, dict) or identity.get('style') != 'mbr':
        return None
    if value.get('usb_target_requested') is not True or value.get('partition_guid') is not None:
        raise ValueError('MBR requires explicit USB selection and no synthetic GPT GUID')
    target = fleet.validate_usb_storage_target({'kind': 'usb-ntfs-mbr-v1', **{k: v for k, v in identity.items() if k != 'style'}})
    if (target['offset_bytes'], target['size_bytes']) != (value.get('partition_offset_bytes'), value.get('partition_size_bytes')):
        raise ValueError('MBR identity geometry differs from candidate')
    return target


def check_candidate(value, hardware, now=None):
    if value.get('schema') != 1 or value.get('kind') != 'windows-standard-candidate':
        raise ValueError('Invalid standard-user candidate')
    if fleet.hardware_uuid(value.get('hardware_uuid')) != hardware:
        raise ValueError('Windows inventory belongs to a different PC')
    if mbr_candidate_target(value) is None:
        fleet.hardware_uuid(value.get('partition_guid'))
    for name in ('partition_offset_bytes', 'partition_size_bytes'):
        if type(value.get(name)) is not int or value[name] <= 0 or value[name] % 512:
            raise ValueError('Invalid partition offset/size')
    if value.get('filesystem') != 'NTFS' or type(value.get('fast_startup_enabled')) is not bool:
        raise ValueError('NTFS and a valid Fast Startup observation must be confirmed')
    if value.get('usb_target_requested') is True:
        if value.get('partition_is_boot') is not False or value.get('partition_is_system') is not False or value.get('personal_os_volume_consent') is not None:
            raise ValueError('USB data target cannot be boot/system/unknown or use OS consent')
        if value.get('partition_flags_pending') is True:
            if value.get('partition_is_hidden', 'missing') is not None:
                raise ValueError('Only explicitly unknown IsHidden may await Linux GPT checks')
        elif value.get('partition_flags_pending') is not False or value.get('partition_is_hidden') is not False:
            raise ValueError('USB hidden/unknown partition flags are forbidden')
        capacity = value.get('capacity_observation')
        if not isinstance(capacity, dict) or capacity.get('root_image_bytes') != storage.DEFAULT_ROOT_BYTES:
            raise ValueError('USB capacity observation must match the current 6GiB root image')
        storage.check_capacity(capacity['root_image_bytes'], capacity.get('available_bytes'), capacity.get('existing_image_bytes'))
    else:
        if value.get('usb_target_requested') not in (None, False) or value.get('partition_flags_pending') not in (None, False):
            raise ValueError('Invalid USB candidate selection')
        fleet.validate_partition_policy(value, 'partition_guid')
    fleet.validate_storage_directory(value.get('storage_directory'))
    checked = value.get('checked_at_unix')
    current = time.time() if now is None else now
    if type(checked) not in (int, float) or not math.isfinite(checked) or checked < 0 or checked > current:
        raise ValueError('Invalid or future-dated Windows candidate timestamp')


def secure_boot_state(efivars=Path('/sys/firmware/efi/efivars')):
    suffix = '-8be4df61-93ca-11d2-aa0d-00e098032b8c'
    states = []
    for name in ('SecureBoot', 'SetupMode'):
        data = (efivars / (name + suffix)).read_bytes()
        if len(data) != 5 or data[4] not in (0, 1):
            raise ValueError('Invalid EFI Secure Boot variable')
        states.append(data[4])
    if states != [1, 0]:
        raise ValueError('Fedora must have SecureBoot=1 and SetupMode=0')
    return True


def shell_evidence_timestamp(evidence, value, hardware, now=None):
    if evidence.get('schema') != 1 or evidence.get('kind') != 'bitlocker-shell-observation':
        raise ValueError('Invalid Shell evidence schema/kind')
    if fleet.hardware_uuid(evidence.get('hardware_uuid')) != hardware:
        raise ValueError('Shell evidence belongs to a different PC')
    mbr = mbr_candidate_target(value)
    if mbr is not None:
        identity = evidence.get('partition_identity')
        if not isinstance(identity, dict) or identity.get('style') != 'mbr':
            raise ValueError('Missing Shell evidence MBR identity')
        attested = fleet.validate_usb_storage_target({'kind': 'usb-ntfs-mbr-v1', **{k: v for k, v in identity.items() if k != 'style'}})
        if attested != mbr or identity != value['partition_identity'] or evidence.get('partition_guid') is not None:
            raise ValueError('Shell evidence MBR identity differs from selected partition')
    elif fleet.hardware_uuid(evidence.get('partition_guid')) != value['partition_guid'].lower():
        raise ValueError('Shell evidence partition GUID differs from the selected partition')
    observation = evidence.get('observation')
    if not isinstance(observation, dict):
        raise ValueError('Missing Shell observation')
    drive = value.get('windows_drive')
    observed_drive = observation.get('volume')
    if not isinstance(drive, str) or not re.fullmatch(r'[A-Za-z]:', drive) or not isinstance(observed_drive, str) or observed_drive.upper() != drive.upper():
        raise ValueError('Shell observation drive letter differs from Windows candidate')
    if observation.get('property') != 'System.Volume.BitLockerProtection':
        raise ValueError('Unexpected Shell property')
    if type(observation.get('raw_code')) is not int or observation['raw_code'] != 2 or observation.get('shell_state') != 'Off':
        raise ValueError('Shell BitLocker state is encrypted, transitioning, suspended, unknown, or not Off')
    if observation.get('off_candidate') is not True or observation.get('requires_linux_verification') is not True:
        raise ValueError('Invalid Shell evidence verification flags')
    if 'error_type' not in observation or observation['error_type'] is not None:
        raise ValueError('Shell observation reported an error or omitted its error state')
    checked = evidence.get('checked_at_unix')
    current = time.time() if now is None else now
    if type(checked) not in (int, float) or not math.isfinite(checked) or checked < 0 or checked > current:
        raise ValueError('Invalid or future-dated Shell evidence timestamp')
    return checked


def complete_candidate(value, hardware, partition, ntfs_uuid, secure_boot, evidence, now=None, usb_verification=None):
    check_candidate(value, hardware, now)
    mbr = mbr_candidate_target(value)
    expected_guid = None if mbr is not None else value['partition_guid'].lower()
    if partition != (expected_guid, value['partition_offset_bytes'], value['partition_size_bytes']):
        raise ValueError('PARTUUID/offset/partition size differs from Windows selection')
    if secure_boot is not True:
        raise ValueError('Linux Secure Boot state is not confirmed')
    if not isinstance(evidence, dict):
        raise ValueError('BitLocker is unknown: supply --bitlocker-evidence; NTFS type alone is insufficient')
    method = 'reviewed-windows-inventory-and-linux-ntfs'
    if evidence.get('kind') == 'bitlocker-shell-observation':
        checked = shell_evidence_timestamp(evidence, value, hardware, now)
        method = 'windows-shell-off-and-linux-raw-ntfs'
    else:
        if evidence.get('schema') != 1 or fleet.hardware_uuid(evidence.get('hardware_uuid')) != hardware or evidence.get('ntfs_uuid', '').upper() != ntfs_uuid.upper():
            raise ValueError('BitLocker evidence does not match this PC and NTFS UUID')
        if evidence.get('bitlocker_volume_status') != 'FullyDecrypted' or evidence.get('bitlocker_protection') != 'Off':
            raise ValueError('BitLocker evidence encrypted/protected/unknown')
        checked = evidence.get('checked_at_unix')
        current = time.time() if now is None else now
        if type(checked) not in (int, float) or not math.isfinite(checked) or checked < 0 or checked > current:
            raise ValueError('Invalid or future-dated BitLocker evidence timestamp')
    if value.get('usb_target_requested') is True:
        checked_target = fleet.validate_usb_storage_target(usb_verification)
        if mbr is not None and checked_target != mbr:
            raise ValueError('MBR verification differs from explicit Windows identity')
        if (checked_target.get('partition_guid'), checked_target['offset_bytes'], checked_target['size_bytes']) != partition:
            raise ValueError('USB GPT verification differs from Windows selection')
        value = {**value, 'partition_is_hidden': False, 'partition_flags_pending': False,
                 'storage_target': checked_target}
    elif usb_verification is not None:
        raise ValueError('USB storage was not explicitly selected in Windows')
    consent = value.get('personal_os_volume_consent')
    if consent is not None:
        value = {**value, 'personal_os_volume_consent': {**consent, 'target': ntfs_uuid.upper()}}
    return {**value, 'ntfs_uuid': ntfs_uuid.upper(), 'bitlocker_verification_method': method,
            'bitlocker_volume_status': 'FullyDecrypted', 'bitlocker_protection': 'Off',
            'secure_boot_enabled': True,
            # Reusing an older attestation must not silently extend its validity.
            'checked_at_unix': min(value['checked_at_unix'], checked)}


def resolve_mbr_candidate(value, hardware, evidence, sysfs=Path('/sys/class/block')):
    expected = mbr_candidate_target(value)
    matches = []
    for entry in sysfs.iterdir():
        node = entry.resolve()
        if not (node / 'partition').is_file():
            continue
        if int((node / 'partition').read_text()) != expected['partition_number']:
            continue
        if (int((node / 'start').read_text()) * 512, int((node / 'size').read_text()) * 512) != (expected['offset_bytes'], expected['size_bytes']):
            continue
        if run('lsblk', '-dn', '-o', 'TRAN', '/dev/' + node.parent.name) != 'usb':
            continue
        device = '/dev/' + entry.name
        verified = storage.inspect_usb_target(device, run)
        if verified == expected:
            matches.append((device, verified))
    if len(matches) != 1:
        raise ValueError('MBR signature/number/geometry must uniquely identify one physical USB partition')
    device, verified = matches[0]
    ntfs_uuid = run('blkid', '-p', '-s', 'UUID', '-o', 'value', device)
    return complete_candidate(value, hardware, (None, expected['offset_bytes'], expected['size_bytes']),
                              ntfs_uuid, secure_boot_state(), evidence, usb_verification=verified)


def resolve_candidate(value, hardware, evidence):
    check_candidate(value, hardware)
    if mbr_candidate_target(value) is not None:
        return resolve_mbr_candidate(value, hardware, evidence)
    guid = value['partition_guid'].lower()
    devices = run('blkid', '-c', '/dev/null', '-t', 'PARTUUID=' + guid, '-o', 'device').splitlines()
    if len(devices) != 1:
        raise ValueError('PARTUUID must uniquely resolve to a raw partition')
    device = str(Path(devices[0]).resolve())
    sysdev = (Path('/sys/class/block') / Path(device).name).resolve()
    if not (sysdev / 'partition').is_file():
        raise ValueError('Expected a physical disk partition, not a decrypted mapper')
    transport = run('lsblk', '-dn', '-o', 'TRAN', '/dev/' + sysdev.parent.name)
    usb_verification = None
    if value.get('usb_target_requested') is True:
        if transport != 'usb':
            raise ValueError('Explicit USB selection does not match actual transport')
        usb_verification = storage.inspect_usb_target(device, run)
    elif transport not in ('nvme', 'sata'):
        raise ValueError('USB target requires explicit Windows -UsbStorageTarget selection')
    actual_guid = run('blkid', '-p', '-s', 'PART_ENTRY_UUID', '-o', 'value', device).lower()
    partition = (actual_guid, int((sysdev / 'start').read_text()) * 512,
                 int((sysdev / 'size').read_text()) * 512)
    if run('blkid', '-p', '-s', 'TYPE', '-o', 'value', device) != 'ntfs':
        raise ValueError('Encrypted, ambiguous, or non-NTFS target; never unlock or repair')
    ntfs_uuid = run('blkid', '-p', '-s', 'UUID', '-o', 'value', device)
    return complete_candidate(value, hardware, partition, ntfs_uuid, secure_boot_state(), evidence, usb_verification=usb_verification)


def registration_context(inventory, gpu_bdf, mount, bitlocker_evidence=None, no_gpu_test=False):
    config_device = live_config(mount)
    value = read_input(inventory)
    original_inventory = value
    evidence = None
    hardware = fleet.hardware_uuid(Path('/sys/class/dmi/id/product_uuid').read_text().strip())
    if hardware != value.get('hardware_uuid', '').lower():
        raise ValueError('Windows inventory belongs to a different PC')
    if value.get('kind') == 'windows-standard-candidate':
        evidence = read_input(bitlocker_evidence) if bitlocker_evidence else value.get('bitlocker_observation')
        value = resolve_candidate(value, hardware, evidence)
    devices = run('blkid', '-c', '/dev/null', '-t', 'UUID=' + value['ntfs_uuid'], '-o', 'device').splitlines()
    if len(devices) != 1 or run('blkid', '-p', '-s', 'TYPE', '-o', 'value', devices[0]) != 'ntfs':
        raise ValueError('NTFS UUID must uniquely identify an NTFS partition')
    if 'storage_target' in value:
        storage.verify_not_reserved_on_boot_usb(devices[0], config_device, run)
        if storage.inspect_usb_target(devices[0], run) != fleet.validate_usb_storage_target(value['storage_target']):
            raise ValueError('USB target GPT identity differs from reviewed inventory')
    else:
        parent = run('lsblk', '-dn', '-o', 'PKNAME', devices[0])
        if not parent or run('lsblk', '-dn', '-o', 'TRAN', '/dev/' + parent) not in ('nvme', 'sata'):
            raise ValueError('USB target requires a new explicit Windows USB candidate and Fedora review')
    if no_gpu_test:
        if gpu_bdf is not None or 'storage_target' not in value:
            raise ValueError('No-GPU test requires USB storage and no GPU BDF')
        for entry in Path('/sys/bus/pci/devices').iterdir():
            if (entry / 'vendor').read_text().strip().lower() == '0x10de':
                raise ValueError('No-GPU test requires a PC without NVIDIA hardware')
        value = {**value, 'boot_mode': 'no-gpu-test'}
        pci_id = None
    else:
        if not re.fullmatch(r'[a-f0-9]{4}:[a-f0-9]{2}:[a-f0-9]{2}\.[0-7]', gpu_bdf):
            raise ValueError('Invalid GPU BDF')
        gpu = Path('/sys/bus/pci/devices') / gpu_bdf
        if not (gpu / 'vendor').is_file() or (gpu / 'vendor').read_text().strip() != '0x10de':
            raise ValueError('GPU BDF does not identify NVIDIA hardware')
        pci_id = '10de:' + (gpu / 'device').read_text().strip().removeprefix('0x').lower()
        if pci_id not in fleet.GPU_NORMAL_IDS:
            raise ValueError('GPU BDF is not an RTX30 PCI ID supported by this release')
    verification = value.get('bitlocker_verification_method')
    value = fleet.record({**value, 'gpu_bdf': gpu_bdf, 'gpu_pci_id': pci_id})
    return {'record': value, 'hardware': hardware, 'verification': verification,
            'original_inventory': original_inventory, 'evidence': evidence}


def registration_digest(context, mount):
    current = mount / 'gpu-config/registry.json'
    snapshot = {'context': context, 'marker': read_input(mount / 'usb-config.json'),
                'registry': read_input(current) if current.exists() else None}
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def register(inventory, gpu_bdf, mount, confirm, bitlocker_evidence=None, expected_review=None, no_gpu_test=False):
    context = registration_context(inventory, gpu_bdf, mount, bitlocker_evidence, no_gpu_test)
    value = context['record']
    hardware = context['hardware']
    verification = context['verification']
    original_inventory = context['original_inventory']
    evidence = context['evidence']
    if expected_review is not None and registration_digest(context, mount) != expected_review:
        raise ValueError('Selection or registry changed after preview; review again')
    print(json.dumps(value, indent=2))
    if 'storage_target' in value:
        print('USB root storage: keep this storage USB connected until shutdown. Capacity is rechecked before the first copy.')
    if confirm != 'REGISTER':
        raise ValueError('Review the selection, then repeat with --confirm REGISTER')
    marker = json.loads((mount / 'usb-config.json').read_text())
    current = mount / 'gpu-config/registry.json'
    inputs = [value]
    generation = 1
    if current.exists():
        old = fleet.load(current)
        inputs.insert(0, old)
        generation = old['generation'] + 1
    registry = fleet.merge(inputs, generation, marker['root_version'])
    # Write verification provenance before committing the registry. This file is
    # never used as authorization; only the validated registry is used at boot.
    if verification:
        reviews = mount / 'gpu-config/registration-reviews'
        reviews.mkdir(exist_ok=True)
        if reviews.is_symlink():
            raise ValueError('Unsafe review directory')
        receipt = {'schema': 1, 'hardware_uuid': hardware, 'ntfs_uuid': value['ntfs_uuid'],
                   'generation': generation, 'checked_at_unix': value['checked_at_unix'],
                   'method': verification, 'secure_boot_source': 'linux-efi-variables',
                   'windows_candidate': original_inventory}
        if bitlocker_evidence:
            receipt['explicit_evidence'] = evidence
        fleet.atomic_write(reviews / (hardware + '-generation-' + str(generation) + '.json'),
                           (json.dumps(receipt, indent=2) + '\n').encode())
    fleet.deploy(registry, mount)
    print('Registered to USB only. NTFS was not mounted or modified.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    status = sub.add_parser('status')
    status.add_argument('registry', type=Path)
    merge = sub.add_parser('merge')
    merge.add_argument('inputs', nargs='+', type=Path)
    merge.add_argument('--generation', type=int, required=True)
    merge.add_argument('--root-version', help='legacy origin label; registry compatibility uses schema')
    merge.add_argument('--output', type=Path, required=True)
    deploy = sub.add_parser('deploy')
    deploy.add_argument('registry', type=Path)
    deploy.add_argument('--mount', type=Path, required=True)
    registration = sub.add_parser('register')
    registration.add_argument('--inventory', type=Path, required=True)
    mode = registration.add_mutually_exclusive_group(required=True)
    mode.add_argument('--gpu-bdf')
    mode.add_argument('--no-gpu-test', action='store_true', help='explicit USB-only test without NVIDIA hardware')
    registration.add_argument('--mount', type=Path, required=True)
    registration.add_argument('--confirm', default='')
    registration.add_argument('--bitlocker-evidence', type=Path, help='saved Shell observation JSON or reviewed Windows BitLocker inventory')
    args = parser.parse_args()
    if args.operation == 'status':
        value = fleet.load(args.registry)
        print(json.dumps(value, indent=2))
    elif args.operation == 'merge':
        if args.output.exists():
            raise ValueError('Choose a new merged output filename')
        value = fleet.merge([read_input(p) for p in args.inputs], args.generation, args.root_version)
        fleet.atomic_write(args.output, fleet.encode(value))
        print('Merged', len(value['records']), 'hosts; generation', value['generation'])
    elif args.operation == 'deploy':
        live_config(args.mount)
        fleet.deploy(fleet.load(args.registry), args.mount)
        print('Registry deployed to this USB configuration partition')
    else:
        register(args.inventory, args.gpu_bdf, args.mount, args.confirm, args.bitlocker_evidence, no_gpu_test=args.no_gpu_test)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        sys.exit('FLEET STOP: ' + str(error))
