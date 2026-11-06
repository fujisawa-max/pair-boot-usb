"""USB support fixtures use GPT byte streams and mocked Windows APIs only."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


storage = load('usb_storage_fixture', 'runtime/storage-target.py')
standard = load('usb_standard_fixture', 'tests/test-standard-inventory.py')
root = load('usb_root_fixture', 'runtime/gpu-root.py')
ready = load('usb_ready_fixture', 'runtime/announce-usb-ready.py')
PWSH = ROOT / 'build/powershell-7.5.4/pwsh'
DISK_GUID = '11111111-2222-3333-4444-555555555555'


def gpt(sector=512, kind=storage.BASIC_DATA, attrs=0, overlap=False):
    sectors = 8192
    data = bytearray(sector * sectors)
    data[510:512] = b'\x55\xaa'
    data[450] = 0xee
    struct.pack_into('<II', data, 454, 1, sectors - 1)
    table = bytearray(128 * 128)
    entry = bytearray(128)
    entry[:16] = uuid.UUID(kind).bytes_le
    entry[16:32] = uuid.UUID(standard.PARTITION).bytes_le
    struct.pack_into('<QQQ', entry, 32, 2048, 4095, attrs)
    table[4 * 128:5 * 128] = entry
    if overlap:
        second = bytearray(entry)
        second[16:32] = uuid.UUID(DISK_GUID).bytes_le
        table[5 * 128:6 * 128] = second
    blocks = len(table) // sector
    data[2 * sector:2 * sector + len(table)] = table
    data[(sectors - 1 - blocks) * sector:(sectors - 1) * sector] = table
    for current, alternate, table_lba in ((1, sectors - 1, 2), (sectors - 1, 1, sectors - 1 - blocks)):
        header = bytearray(sector)
        struct.pack_into('<8sIIIIQQQQ', header, 0, b'EFI PART', 0x10000, 92, 0, 0,
                         current, alternate, 2 + blocks, sectors - 2 - blocks)
        header[56:72] = uuid.UUID(DISK_GUID).bytes_le
        struct.pack_into('<QIII', header, 72, table_lba, 128, 128, zlib.crc32(table))
        struct.pack_into('<I', header, 16, zlib.crc32(header[:92]))
        data[current * sector:(current + 1) * sector] = header
    return data


def usb_metadata():
    return {'kind': 'usb-ntfs-v1', 'partition_guid': standard.PARTITION, 'disk_guid': DISK_GUID,
            'offset_bytes': 1048576, 'size_bytes': 8 * 1024**3}


def usb_candidate():
    return {**standard.candidate(), 'usb_target_requested': True, 'partition_flags_pending': True,
            'partition_is_hidden': None,
            'capacity_observation': {'root_image_bytes': storage.DEFAULT_ROOT_BYTES,
                                     'available_bytes': 8 * 1024**3, 'existing_image_bytes': None}}


class UsbStorageTests(unittest.TestCase):
    def test_gpt_512_and_4096_and_no_default_drive_letter(self):
        for sector in (512, 4096):
            for attrs in (0, 1 << 63):
                image = gpt(sector, attrs=attrs)
                selected = storage.read_gpt_partition(io.BytesIO(image), sector, len(image), 5)
                self.assertEqual(selected['partition_guid'], standard.PARTITION)
                self.assertEqual(selected['offset_bytes'], 2048 * sector)
                self.assertEqual(selected['size_bytes'], 2048 * sector)

    def test_gpt_hidden_readonly_required_unknown_and_efi_are_rejected(self):
        for bit in (0, 1, 2, 3, 48, 60, 61, 62):
            image = gpt(attrs=1 << bit)
            with self.subTest(bit=bit), self.assertRaisesRegex(ValueError, 'attributes'):
                storage.read_gpt_partition(io.BytesIO(image), 512, len(image), 5)
        for kind in ('c12a7328-f81f-11d2-ba4b-00a0c93ec93b',
                     'de94bba4-06d1-4d40-a16a-bfd50179d6ac',
                     'e3c9e316-0b5c-4db8-817d-f92df00215ae'):
            image = gpt(kind=kind)
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'basic data'):
                storage.read_gpt_partition(io.BytesIO(image), 512, len(image), 5)

    def test_corruption_backup_and_overlap_are_rejected(self):
        for offset in (512 + 16, 2 * 512 + 4 * 128 + 16, -512 + 16, -1024):
            image = gpt()
            image[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                storage.read_gpt_partition(io.BytesIO(image), 512, len(image), 5)
        image = gpt(overlap=True)
        with self.assertRaisesRegex(ValueError, 'Overlapping'):
            storage.read_gpt_partition(io.BytesIO(image), 512, len(image), 5)
        image = gpt()
        image[466] = 7  # hybrid MBR partition type
        with self.assertRaises(ValueError):
            storage.read_gpt_partition(io.BytesIO(image), 512, len(image), 5)

    def test_capacity_first_copy_existing_and_invalid_sizes(self):
        root_bytes = 6 * 1024**3
        self.assertEqual(storage.capacity_requirement(root_bytes), 7 * 1024**3)
        with self.assertRaisesRegex(ValueError, 'Insufficient'):
            storage.check_capacity(root_bytes, 3 * 1024**3)
        storage.check_capacity(root_bytes, 7 * 1024**3)
        value = storage.check_capacity(root_bytes, 0, root_bytes)
        self.assertEqual(value['mode'], 'existing-image-needs-boot-validation')
        for free in (None, '999999999999', True, -1):
            with self.subTest(free=free), self.assertRaises(ValueError):
                storage.check_capacity(root_bytes, free)
        with self.assertRaisesRegex(ValueError, 'never overwrite'):
            storage.check_capacity(root_bytes, 8 * 1024**3, root_bytes - 1)

    def test_pending_flags_require_native_gpt_proof_before_final_record(self):
        value = usb_candidate()
        args = (value, standard.HARDWARE, (standard.PARTITION, 1048576, 8 * 1024**3),
                '0123456789ABCDEF', True, standard.evidence())
        with self.assertRaises(ValueError):
            standard.tool.complete_candidate(*args, now=standard.NOW)
        completed = standard.tool.complete_candidate(*args, now=standard.NOW, usb_verification=usb_metadata())
        self.assertIs(completed['partition_is_hidden'], False)
        record = standard.tool.fleet.record({**completed, 'gpu_bdf': '0000:01:00.0', 'gpu_pci_id': '10de:2504'}, now=standard.NOW)
        self.assertEqual(record['storage_target'], usb_metadata())
        self.assertNotIn('partition_flags_pending', record)
        for field, changed in [('partition_is_hidden', True), ('partition_is_system', None),
                               ('partition_is_boot', True), ('partition_flags_pending', False)]:
            bad = {**value, field: changed}
            with self.subTest(field=field), self.assertRaises(ValueError):
                standard.tool.check_candidate(bad, standard.HARDWARE, standard.NOW)
        bad = copy.deepcopy(value)
        bad['capacity_observation']['available_bytes'] = 3 * 1024**3
        with self.assertRaisesRegex(ValueError, 'Insufficient'):
            standard.tool.check_candidate(bad, standard.HARDWARE, standard.NOW)
        with self.assertRaises(ValueError):
            standard.tool.complete_candidate(*args, now=standard.NOW,
                                             usb_verification={**usb_metadata(), 'size_bytes': 512})

    def test_same_usb_must_remain_connected_even_if_mount_inventory_is_empty(self):
        state = {'usb_device': '/dev/sde2', 'usb_unmounted': True,
                 'root_storage_on_boot_usb': True, 'root_storage_transport': 'usb'}
        with self.assertRaisesRegex(ValueError, 'keep it inserted'):
            ready.verify(state, lambda *args: self.fail('Must reject before mount inventory'))

    def test_boot_checks_usb_identity_and_requires_explicit_metadata(self):
        metadata = usb_metadata()
        policy = type('Policy', (), {'inspect_usb_target': lambda *args: metadata})()
        def command(*args):
            if 'TRAN' in args:
                return 'usb'
            return 'sde' if args[-1] == '/dev/sde2' else 'sdf'
        with patch.object(root, 'run', side_effect=command), patch.object(root, 'storage_policy', return_value=policy):
            with self.assertRaisesRegex(root.SafetyError, 'explicit'):
                root.verify_root_storage({}, '/dev/sdf5', '/dev/sde2')
            state = root.verify_root_storage({'storage_target': metadata}, '/dev/sdf5', '/dev/sde2')
            self.assertIs(state['root_storage_on_boot_usb'], False)
            with self.assertRaisesRegex(root.SafetyError, 'identity changed'):
                root.verify_root_storage({'storage_target': {**metadata, 'size_bytes': 512}}, '/dev/sdf5', '/dev/sde2')

    def test_same_usb_partition_five_is_allowed_but_reserved_four_is_not(self):
        metadata = usb_metadata()
        policy = type('Policy', (), {'inspect_usb_target': lambda *args: metadata})()
        with tempfile.TemporaryDirectory() as folder:
            sysfs = Path(folder)
            for name, number in (('sde5', '5'), ('sde4', '4')):
                (sysfs / name).mkdir()
                (sysfs / name / 'partition').write_text(number)
            actual_path = Path
            def paths(value):
                return sysfs if str(value) == '/sys/class/block' else actual_path(value)
            def command(*args):
                return 'usb' if 'TRAN' in args else 'sde'
            with patch.object(root, 'run', side_effect=command), patch.object(root, 'storage_policy', return_value=policy), patch.object(root, 'Path', side_effect=paths):
                state = root.verify_root_storage({'storage_target': metadata}, '/dev/sde5', '/dev/sde2')
                self.assertIs(state['root_storage_on_boot_usb'], True)
                with self.assertRaisesRegex(root.SafetyError, 'reserved'):
                    root.verify_root_storage({'storage_target': metadata}, '/dev/sde4', '/dev/sde2')

    def test_raw_usb_inspection_uses_only_readonly_fixture_and_kernel_geometry(self):
        image = gpt()
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            disk = work / 'disk.img'
            disk.write_bytes(image)
            sysfs = work / 'sys'
            partition = sysfs / 'sdf/sdf5'
            partition.mkdir(parents=True)
            (sysfs / 'sdf5').symlink_to(partition)
            for name, value in (('partition', '5'), ('start', '2048'), ('size', '2048')):
                (partition / name).write_text(value)
            actual_open = os.open
            def fixture_open(path, flags):
                self.assertEqual(str(path), '/dev/sdf')
                self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
                return actual_open(disk, flags)
            def command(*args):
                if 'MOUNTPOINTS' in args:
                    return ''
                if 'TRAN' in args:
                    return 'usb'
                return 'ntfs' if 'TYPE' in args else standard.PARTITION
            def ioctl(fd, operation, buffer):
                return struct.pack('I', 512) if operation == 0x1268 else struct.pack('Q', len(image))
            with patch.object(storage.os, 'open', side_effect=fixture_open), patch.object(storage.stat, 'S_ISBLK', return_value=True), patch.object(storage.fcntl, 'ioctl', side_effect=ioctl):
                selected = storage.inspect_usb_target('/dev/sdf5', command, sysfs)
                self.assertEqual(selected['partition_guid'], standard.PARTITION)
                (partition / 'size').write_text('2047')
                with self.assertRaisesRegex(ValueError, 'geometry disagree'):
                    storage.inspect_usb_target('/dev/sdf5', command, sysfs)

    def test_registration_rejects_reserved_same_usb_data_partition(self):
        with tempfile.TemporaryDirectory() as folder:
            sysfs = Path(folder)
            part = sysfs / 'sde3'
            part.mkdir()
            (part / 'partition').write_text('3')
            def command(*args):
                return 'sde'
            with self.assertRaisesRegex(ValueError, 'reserved'):
                storage.verify_not_reserved_on_boot_usb('/dev/sde3', '/dev/sde3', command, sysfs)
            (part / 'partition').write_text('5')
            storage.verify_not_reserved_on_boot_usb('/dev/sde3', '/dev/sde3', command, sysfs)

    @unittest.skipUnless(PWSH.exists(), 'Optional workspace PowerShell needed')
    def test_windows_usb_null_flags_and_capacity_precheck(self):
        env = {**os.environ, 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
               'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
               'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')}
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as folder:
            work = Path(folder)
            for free, hidden, expected in ((8 * 1024**3, '$null', 0), (3 * 1024**3, '$null', 1),
                                          (8 * 1024**3, '$true', 1), (8 * 1024**3, "'False'", 1)):
                with self.subTest(free=free, hidden=hidden):
                    output = work / ('candidate-' + str(free) + '-' + hidden.replace("'", '').replace('$', '') + '.json')
                    wrapper = work / 'usb.ps1'
                    wrapper.write_text('''
New-PSDrive -Name F -PSProvider FileSystem -Root "''' + str(work) + '''" | Out-Null
function Get-CimInstance { [pscustomobject]@{UUID='01234567-89ab-cdef-0123-456789abcdef'} }
function Get-Volume { [pscustomobject]@{FileSystem='NTFS';Size=[long]17179869184;SizeRemaining=[long]''' + str(free) + '''} }
function Get-Partition { [pscustomobject]@{GptType='{ebd0a0a2-b9e5-4433-87c0-68b6b72699c7}';Guid='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';Offset=1048576;Size=[long]8589934592;IsBoot=$false;IsSystem=$false;IsHidden=''' + hidden + '''} }
function Get-ItemProperty { [pscustomobject]@{HiberbootEnabled=[int]0} }
& "''' + str(ROOT / 'toolkit/export-standard-inventory.ps1') + '" -Volume F: -UsbStorageTarget -OutputFile "' + str(output) + '"\nexit $LASTEXITCODE\n', encoding='utf-8-sig')
                    result = subprocess.run([str(PWSH), '-NoProfile', '-NonInteractive', '-File', str(wrapper)],
                                            env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, expected, result.stderr)
                    self.assertEqual(output.exists(), expected == 0)
                    if expected == 0:
                        value = json.loads(output.read_text())
                        self.assertIsNone(value['partition_is_hidden'])
                        self.assertTrue(value['partition_flags_pending'])
                        self.assertEqual(value['capacity_observation']['required_bytes'], 7 * 1024**3)
                    elif free < 7 * 1024**3:
                        self.assertIn('空き容量が不足', result.stderr)
