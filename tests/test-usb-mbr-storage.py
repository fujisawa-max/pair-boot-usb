"""MBR USB storage: byte-stream and mocked API fixtures, never real disks."""
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

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixtures = load('mbr_usb_fixtures', 'tests/test-usb-root-storage.py')
standard = fixtures.standard
storage = fixtures.storage
SIGNATURE = 'e1234567'


def target():
    return {'kind': 'usb-ntfs-mbr-v1', 'disk_signature': SIGNATURE, 'partition_number': 1,
            'offset_bytes': 1048576, 'size_bytes': 8 * 1024**3}


def candidate():
    value = fixtures.usb_candidate()
    value['partition_guid'] = None
    value['partition_identity'] = {'style': 'mbr', **{k: v for k, v in target().items() if k != 'kind'}}
    return value


def proof():
    value = standard.ShellRegistrationTests().shell()
    value['partition_guid'] = None
    value['partition_identity'] = candidate()['partition_identity']
    return value


def mbr(sector=512, kind=7, active=0, signature=int(SIGNATURE, 16), overlap=False):
    data = bytearray(8192 * sector)
    data[510:512] = b'\x55\xaa'
    struct.pack_into('<I', data, 440, signature)
    data[446] = active
    data[450] = kind
    struct.pack_into('<II', data, 454, 2048, 2048)
    if overlap:
        data[466] = 7
        struct.pack_into('<II', data, 470, 3000, 2048)
    return data


class MbrStorageTests(unittest.TestCase):
    def test_nonactive_primary_ntfs_512_and_4096(self):
        for sector in (512, 4096):
            image = mbr(sector)
            selected = storage.read_mbr_partition(io.BytesIO(image), sector, len(image), 1)
            self.assertEqual(selected['disk_signature'], SIGNATURE)
            self.assertEqual(selected['offset_bytes'], 2048 * sector)
            self.assertEqual(selected['kind'], 'usb-ntfs-mbr-v1')

    def test_hidden_active_extended_dynamic_and_missing_signature_rejected(self):
        for kind in (0x17, 0x27, 0xef, 0x05, 0x0f, 0x85, 0x42, 0xee):
            image = mbr(kind=kind)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                storage.read_mbr_partition(io.BytesIO(image), 512, len(image), 1)
        for image in (mbr(active=0x80), mbr(signature=0), mbr(signature=0xffffffff), mbr(overlap=True)):
            with self.assertRaises(ValueError):
                storage.read_mbr_partition(io.BytesIO(image), 512, len(image), 1)
        image = mbr()
        image[512:520] = b'EFI PART'
        with self.assertRaisesRegex(ValueError, 'GPT'):
            storage.read_mbr_partition(io.BytesIO(image), 512, len(image), 1)

    def test_bound_mbr_proof_and_registry_roundtrip(self):
        value = candidate()
        args = (value, standard.HARDWARE, (None, 1048576, 8 * 1024**3), '0123456789ABCDEF', True, proof())
        result = standard.tool.complete_candidate(*args, now=standard.NOW, usb_verification=target())
        host = standard.tool.fleet.record({**result, 'gpu_bdf': '0000:01:00.0', 'gpu_pci_id': '10de:2504'}, now=standard.NOW)
        self.assertEqual(host['storage_target'], target())
        registry = standard.tool.fleet.merge([host], 1, 'mbr-fixture', now=standard.NOW)
        self.assertEqual(standard.tool.fleet.decode(standard.tool.fleet.encode(registry), now=standard.NOW), registry)
        for field, changed in (('disk_signature', '12345678'), ('partition_number', 2), ('offset_bytes', 512)):
            bad = copy.deepcopy(proof())
            bad['partition_identity'][field] = changed
            with self.subTest(field=field), self.assertRaises(ValueError):
                standard.tool.complete_candidate(*args[:-1], bad, now=standard.NOW, usb_verification=target())
        with self.assertRaises(ValueError):
            standard.tool.complete_candidate(*args, now=standard.NOW, usb_verification={**target(), 'disk_signature': '12345678'})

    def test_signature_and_primary_number_types_are_strict(self):
        for key, changed in (('disk_signature', '00000000'), ('disk_signature', 'ffffffff'),
                             ('disk_signature', 'E1234567'), ('partition_number', True),
                             ('partition_number', 5), ('size_bytes', 513)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                standard.tool.fleet.validate_usb_storage_target({**target(), key: changed})

    def test_native_matching_is_unique_and_never_mounts(self):
        with tempfile.TemporaryDirectory() as folder:
            sysfs = Path(folder)
            def add(disk):
                real = sysfs / disk / (disk + '1')
                real.mkdir(parents=True)
                for name, value in (('partition', 1), ('start', 2048), ('size', 8 * 1024**3 // 512)):
                    (real / name).write_text(str(value))
                (sysfs / (disk + '1')).symlink_to(real)
            add('sdf')
            def command(*args):
                self.assertNotIn(args[0], ('mount', 'umount', 'ntfs-3g'))
                return 'usb' if 'TRAN' in args else '0123456789ABCDEF'
            with patch.object(standard.tool, 'run', side_effect=command), patch.object(standard.tool.storage, 'inspect_usb_target', return_value=target()), patch.object(standard.tool, 'secure_boot_state', return_value=True), patch.object(standard.tool.time, 'time', return_value=standard.NOW):
                result = standard.tool.resolve_mbr_candidate(candidate(), standard.HARDWARE, proof(), sysfs)
                self.assertEqual(result['storage_target'], target())
                add('sdg')
                with self.assertRaisesRegex(ValueError, 'uniquely'):
                    standard.tool.resolve_mbr_candidate(candidate(), standard.HARDWARE, proof(), sysfs)

    def test_raw_inspector_dispatches_mbr_without_requiring_gpt_guid(self):
        image = mbr()
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            disk = work / 'usb.img'
            disk.write_bytes(image)
            sysfs = work / 'sys'
            real = sysfs / 'sdf/sdf1'
            real.mkdir(parents=True)
            (sysfs / 'sdf1').symlink_to(real)
            for name, value in (('partition', 1), ('start', 2048), ('size', 2048)):
                (real / name).write_text(str(value))
            original_open = os.open
            def file_only(path, flags):
                self.assertEqual(str(path), '/dev/sdf')
                self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
                return original_open(disk, flags)
            def command(*args):
                if 'MOUNTPOINTS' in args:
                    return ''
                if 'TRAN' in args:
                    return 'usb'
                self.assertIn('TYPE', args)
                return 'ntfs'
            def ioctl(fd, operation, buffer):
                return struct.pack('I', 512) if operation == 0x1268 else struct.pack('Q', len(image))
            with patch.object(storage.os, 'open', side_effect=file_only), patch.object(storage.stat, 'S_ISBLK', return_value=True), patch.object(storage.fcntl, 'ioctl', side_effect=ioctl):
                selected = storage.inspect_usb_target('/dev/sdf1', command, sysfs)
                self.assertEqual(selected['kind'], 'usb-ntfs-mbr-v1')
                self.assertEqual(selected['disk_signature'], SIGNATURE)

    @unittest.skipUnless(fixtures.PWSH.exists(), 'Workspace PowerShell optional')
    def test_windows_nonadmin_mbr_inventory_and_missing_signature(self):
        env = {**os.environ, 'DOTNET_SYSTEM_GLOBALIZATION_INVARIANT': '1',
               'XDG_CACHE_HOME': str(ROOT / '.cache/powershell-xdg'),
               'XDG_CONFIG_HOME': str(ROOT / '.cache/powershell-config')}
        with tempfile.TemporaryDirectory(dir=ROOT / 'build') as folder:
            work = Path(folder)
            for sig, expected in ((int(SIGNATURE, 16), 0), (0, 1), (None, 1)):
                with self.subTest(signature=sig):
                    output = work / ('candidate-' + str(sig) + '.json')
                    script = work / 'inventory.ps1'
                    signature_value = '$null' if sig is None else '[uint32]' + str(sig)
                    script.write_text('''
New-PSDrive -Name D -PSProvider FileSystem -Root "''' + str(work) + '''" | Out-Null
function Get-CimInstance { param($ClassName,$Filter)
 if ($ClassName -eq 'Win32_DiskDrive') { [pscustomobject]@{Index=1;Signature=''' + signature_value + '''} }
 else { [pscustomobject]@{UUID='01234567-89ab-cdef-0123-456789abcdef'} }
}
function Get-Volume { [pscustomobject]@{FileSystem='NTFS';Size=[long]15500000000;SizeRemaining=[long]14000000000} }
function Get-Partition { [pscustomobject]@{Guid=$null;GptType=$null;MbrType=[uint16]7;DiskNumber=1;PartitionNumber=1;Offset=[long]1048576;Size=[long]8589934592;IsBoot=$false;IsSystem=$false;IsHidden=$null} }
function Get-ItemProperty { [pscustomobject]@{HiberbootEnabled=[int]0} }
& "''' + str(ROOT / 'toolkit/export-standard-inventory.ps1') + '" -Volume D: -UsbStorageTarget -OutputFile "' + str(output) + '"\nexit $LASTEXITCODE\n', encoding='utf-8-sig')
                    result = subprocess.run([str(fixtures.PWSH), '-NoProfile', '-NonInteractive', '-File', str(script)], env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, expected, result.stderr)
                    self.assertEqual(output.exists(), expected == 0)
                    if expected == 0:
                        value = json.loads(output.read_text())
                        self.assertIsNone(value['partition_guid'])
                        self.assertEqual(value['partition_identity']['disk_signature'], SIGNATURE)
                        self.assertEqual(value['bitlocker_observation']['partition_identity'], value['partition_identity'])
                    else:
                        self.assertIn('ディスク署名', result.stderr)
                        self.assertNotIn('CategoryInfo', result.stderr)
