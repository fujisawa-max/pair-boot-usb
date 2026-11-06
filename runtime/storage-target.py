#!/usr/bin/env python3
"""Read-only USB GPT validation and capacity calculations. Never format or repair."""
import fcntl
import os
from pathlib import Path
import stat
import struct
import uuid
import zlib

BASIC_DATA = 'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7'
DEFAULT_ROOT_BYTES = 6 * 1024**3
MAX_TABLE_BYTES = 16 * 1024**2


def capacity_requirement(root_bytes):
    if type(root_bytes) is not int or not 16 * 1024**2 <= root_bytes <= 1024**4:
        raise ValueError('Invalid root image size')
    return root_bytes + max(1024**3, root_bytes // 10)


def check_capacity(root_bytes, available_bytes, existing_image_bytes=None):
    required = capacity_requirement(root_bytes)
    if type(available_bytes) is not int or available_bytes < 0:
        raise ValueError('Free space is unknown')
    if existing_image_bytes is not None:
        if type(existing_image_bytes) is not int or existing_image_bytes != root_bytes:
            raise ValueError('Existing root.img size differs; never overwrite it')
        # Mutable images are validated by the boot controller, not this estimate.
        return {'mode': 'existing-image-needs-boot-validation', 'required_bytes': 0,
                'available_bytes': available_bytes, 'root_image_bytes': root_bytes}
    if available_bytes < required:
        raise ValueError(f'Insufficient space: need {required} bytes, available {available_bytes} bytes')
    return {'mode': 'first-copy', 'required_bytes': required,
            'available_bytes': available_bytes, 'root_image_bytes': root_bytes}


def _read(stream, offset, size):
    stream.seek(offset)
    data = stream.read(size)
    if len(data) != size:
        raise ValueError('Truncated GPT')
    return data


def read_gpt_partition(stream, sector_size, disk_bytes, number):
    """Pure parser for regular-file fixtures or an already read-only disk stream."""
    if sector_size not in (512, 4096) or disk_bytes % sector_size or disk_bytes < 68 * sector_size:
        raise ValueError('Unsupported disk geometry')
    if type(number) is not int or number < 1:
        raise ValueError('Invalid GPT partition number')
    last_lba = disk_bytes // sector_size - 1
    mbr = _read(stream, 0, 512)
    entries = [mbr[446 + i * 16:462 + i * 16] for i in range(4)]
    protective = [entry for entry in entries if entry[4] == 0xee]
    if mbr[510:512] != b'\x55\xaa' or len(protective) != 1 or any(entry[4] not in (0, 0xee) for entry in entries):
        raise ValueError('Expected protective GPT MBR, not MBR/hybrid')
    if struct.unpack_from('<I', protective[0], 8)[0] != 1:
        raise ValueError('Invalid protective MBR start')

    def header(lba):
        raw = _read(stream, lba * sector_size, sector_size)
        signature, revision, size, crc, reserved = struct.unpack_from('<8sIIII', raw)
        if signature != b'EFI PART' or revision != 0x10000 or not 92 <= size <= sector_size or reserved:
            raise ValueError('Invalid GPT header')
        checked = bytearray(raw[:size])
        checked[16:20] = b'\0' * 4
        if zlib.crc32(checked) != crc:
            raise ValueError('GPT header CRC mismatch')
        current, alternate, first, last = struct.unpack_from('<QQQQ', raw, 24)
        disk_guid = str(uuid.UUID(bytes_le=raw[56:72]))
        table_lba, count, entry_size, table_crc = struct.unpack_from('<QIII', raw, 72)
        table_size = count * entry_size
        if current != lba or alternate != (last_lba if lba == 1 else 1) or not 2 <= first <= last < last_lba:
            raise ValueError('Invalid GPT header locations')
        if disk_guid == str(uuid.UUID(int=0)) or count < number or entry_size < 128 or entry_size & (entry_size - 1) or not 16384 <= table_size <= MAX_TABLE_BYTES:
            raise ValueError('Invalid GPT entry geometry')
        blocks = (table_size + sector_size - 1) // sector_size
        table_fits = (2 <= table_lba and table_lba + blocks <= first) if lba == 1 else (last < table_lba and table_lba + blocks <= last_lba)
        if not table_fits:
            raise ValueError('GPT table overlaps usable data')
        table = _read(stream, table_lba * sector_size, table_size)
        if zlib.crc32(table) != table_crc:
            raise ValueError('GPT entry CRC mismatch')
        return (first, last, disk_guid, count, entry_size), table

    primary, table = header(1)
    backup, backup_table = header(last_lba)
    if primary != backup or table != backup_table:
        raise ValueError('Primary/backup GPT disagree; manual review required')
    first, last, disk_guid, count, entry_size = primary
    spans = []
    identifiers = set()
    selected = None
    for index in range(count):
        entry = table[index * entry_size:(index + 1) * entry_size]
        if entry[:16] == b'\0' * 16:
            continue
        kind = str(uuid.UUID(bytes_le=entry[:16]))
        guid = str(uuid.UUID(bytes_le=entry[16:32]))
        start, end, attrs = struct.unpack_from('<QQQ', entry, 32)
        if not first <= start <= end <= last or guid in identifiers or guid == str(uuid.UUID(int=0)):
            raise ValueError('Invalid/duplicate GPT partition entry')
        identifiers.add(guid)
        spans.append((start, end))
        if index + 1 == number:
            selected = {'kind': 'usb-ntfs-v1', 'partition_guid': guid, 'disk_guid': disk_guid,
                        'offset_bytes': start * sector_size, 'size_bytes': (end - start + 1) * sector_size}
            if kind != BASIC_DATA:
                raise ValueError('USB root target must be a GPT basic data partition')
            # Only no-default-drive-letter is permitted. Hidden, readonly,
            # shadow-copy, required, legacy boot and unknown bits all fail closed.
            if attrs & ~(1 << 63):
                raise ValueError('USB GPT attributes are hidden/readonly/required/unknown')
    spans.sort()
    if any(left[1] >= right[0] for left, right in zip(spans, spans[1:])):
        raise ValueError('Overlapping GPT partitions')
    if selected is None:
        raise ValueError('Missing selected GPT partition')
    return selected


def read_mbr_partition(stream, sector_size, disk_bytes, number):
    """Validate an ordinary primary NTFS MBR entry without writing or repairing."""
    if sector_size not in (512, 4096) or disk_bytes % sector_size or disk_bytes < 2 * sector_size or type(number) is not int or not 1 <= number <= 4:
        raise ValueError('Unsupported MBR geometry/primary partition number')
    raw = _read(stream, 0, 512)
    signature = struct.unpack_from('<I', raw, 440)[0]
    if raw[510:512] != b'\x55\xaa' or signature in (0, 0xffffffff):
        raise ValueError('Invalid/missing MBR signature')
    if _read(stream, sector_size, 8) == b'EFI PART' or _read(stream, disk_bytes - sector_size, 8) == b'EFI PART':
        raise ValueError('Ambiguous/stale GPT on MBR disk')
    spans = []
    selected = None
    for index in range(4):
        entry = raw[446 + index * 16:462 + index * 16]
        kind = entry[4]
        if kind == 0:
            if entry != b'\0' * 16:
                raise ValueError('Ambiguous unused MBR entry')
            continue
        if kind in (0xee, 0x05, 0x0f, 0x85, 0x42):
            raise ValueError('Hybrid/GPT/extended/dynamic MBR layout is not supported')
        if entry[0] not in (0, 0x80):
            raise ValueError('Invalid MBR boot indicator')
        start, count = struct.unpack_from('<II', entry, 8)
        if start < 1 or count == 0 or (start + count) * sector_size > disk_bytes:
            raise ValueError('Invalid MBR partition bounds')
        spans.append((start, start + count))
        if index + 1 == number:
            if kind != 0x07 or entry[0] != 0:
                raise ValueError('MBR USB root must be non-active primary type 07 data')
            selected = {'kind': 'usb-ntfs-mbr-v1', 'disk_signature': f'{signature:08x}',
                        'partition_number': number, 'offset_bytes': start * sector_size,
                        'size_bytes': count * sector_size}
    spans.sort()
    if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
        raise ValueError('Overlapping MBR partitions')
    if selected is None:
        raise ValueError('Missing selected MBR primary partition')
    return selected


def inspect_usb_target(device, command, sysfs=Path('/sys/class/block')):
    """Inspect physical USB partition. The only open here is O_RDONLY."""
    device = str(Path(device).resolve())
    node = (sysfs / Path(device).name).resolve()
    if not (node / 'partition').is_file():
        raise ValueError('Expected physical USB partition, not mapper/whole disk')
    number = int((node / 'partition').read_text())
    parent = node.parent
    disk = '/dev/' + parent.name
    if command('lsblk', '-n', '-o', 'MOUNTPOINTS', device).strip():
        raise ValueError('USB target is already mounted; unmount it safely before registration/boot')
    if command('lsblk', '-dn', '-o', 'TRAN', disk).lower() != 'usb':
        raise ValueError('Explicit USB target does not identify USB transport')
    fd = os.open(disk, os.O_RDONLY | os.O_CLOEXEC)
    try:
        if not stat.S_ISBLK(os.fstat(fd).st_mode):
            raise ValueError('Expected physical USB block disk')
        sector_size = struct.unpack('I', fcntl.ioctl(fd, 0x1268, b'\0' * 4))[0]  # BLKSSZGET
        disk_bytes = struct.unpack('Q', fcntl.ioctl(fd, 0x80081272, b'\0' * 8))[0]  # BLKGETSIZE64
        with os.fdopen(os.dup(fd), 'rb') as stream:
            mbr = _read(stream, 0, 512)
            protective = any(mbr[446 + i * 16 + 4] == 0xee for i in range(4))
            parser = read_gpt_partition if protective else read_mbr_partition
            selected = parser(stream, sector_size, disk_bytes, number)
    finally:
        os.close(fd)
    expected = (int((node / 'start').read_text()) * 512, int((node / 'size').read_text()) * 512)
    if (selected['offset_bytes'], selected['size_bytes']) != expected:
        raise ValueError('Kernel and raw GPT geometry disagree')
    if command('blkid', '-p', '-s', 'TYPE', '-o', 'value', device) != 'ntfs':
        raise ValueError('USB partition is encrypted/unknown/non-NTFS')
    if selected['kind'] == 'usb-ntfs-v1' and command('blkid', '-p', '-s', 'PART_ENTRY_UUID', '-o', 'value', device).lower() != selected['partition_guid']:
        raise ValueError('Kernel/probe and GPT partition GUID disagree')
    return selected


def verify_not_reserved_on_boot_usb(device, config_device, command, sysfs=Path('/sys/class/block')):
    target_parent = command('lsblk', '-dn', '-o', 'PKNAME', str(device))
    config_parent = command('lsblk', '-dn', '-o', 'PKNAME', str(config_device))
    if not target_parent or not config_parent:
        raise ValueError('Cannot identify USB target/configuration parents')
    if target_parent == config_parent:
        node = (sysfs / Path(device).name).resolve()
        number = int((node / 'partition').read_text())
        if number < 5:
            raise ValueError('USB root target overlaps reserved boot/master/config/Live partitions')
