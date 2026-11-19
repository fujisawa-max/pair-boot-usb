"""Read-only GPT/FAT/ext4 inspection for owner-built update bundles."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import tempfile
import unicodedata
import uuid
import zlib

CHUNK = 8 * 1024**2
ZERO = b'\0' * CHUNK


def ordinary(path, directory=False):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('Symlink artifact path')
    mode = path.stat().st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise ValueError('Ordinary artifact file/directory required')
    return path


def digest(path):
    with ordinary(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def copy_region(source, target, offset, size, expected=None):
    ordinary(source)
    ordinary(target.parent, True)
    if offset < 0 or size < 1 or offset + size > source.stat().st_size:
        raise ValueError('Invalid artifact region')
    h = hashlib.sha256()
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        incoming.seek(offset)
        left = size
        while left:
            block = incoming.read(min(left, CHUNK))
            if not block:
                raise ValueError('Truncated artifact region')
            h.update(block)
            if block == ZERO[:len(block)]:
                outgoing.seek(len(block), 1)
            else:
                outgoing.write(block)
            left -= len(block)
        outgoing.truncate(size)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    if expected is not None and h.hexdigest() != expected:
        raise ValueError('Artifact SHA-256 differs from verification evidence')
    return {'bytes': size, 'sha256': h.hexdigest()}


def read_gpt(path, layout):
    path = ordinary(path)
    size = path.stat().st_size
    sector = layout['logical_sector_bytes']
    if sector != 512 or size % sector:
        raise ValueError('GPT512 image required')
    with path.open('rb') as stream:
        mbr = stream.read(512)
        if mbr[510:] != b'\x55\xaa' or mbr[450] != 0xee or any(mbr[462:510]):
            raise ValueError('Protective GPT MBR required')
        headers = []
        for lba in (1, size // sector - 1):
            stream.seek(lba * sector); raw = stream.read(sector)
            length = struct.unpack_from('<I', raw, 12)[0]
            if (raw[:8] != b'EFI PART' or not 92 <= length <= 512
                    or struct.unpack_from('<I',raw,8)[0] != 0x10000 or raw[20:24] != b'\0'*4):
                raise ValueError('Invalid GPT header')
            checked = bytearray(raw[:length]); checked[16:20] = b'\0' * 4
            if zlib.crc32(checked) != struct.unpack_from('<I', raw, 16)[0]:
                raise ValueError('GPT header CRC mismatch')
            current, backup, first, last = struct.unpack_from('<QQQQ', raw, 24)
            entries_lba, count, entry_size, crc = struct.unpack_from('<QIII', raw, 72)
            if (current != lba or backup != (size // sector - 1 if lba == 1 else 1)
                    or not 2 <= first <= last < size // sector - 1
                    or not 4 <= count <= 4096 or entry_size < 128 or entry_size % 8
                    or count * entry_size > 1024**2 or entries_lba * sector + count * entry_size > size):
                raise ValueError('Invalid GPT geometry')
            table_end = entries_lba * sector + count * entry_size
            if (not any(raw[56:72]) or entries_lba < 2
                    or lba == 1 and table_end > first * sector
                    or lba != 1 and (entries_lba <= last or table_end > lba * sector)):
                raise ValueError('GPT metadata overlaps usable space')
            stream.seek(entries_lba * sector); table = stream.read(count * entry_size)
            if zlib.crc32(table) != crc:
                raise ValueError('GPT partition CRC mismatch')
            headers.append((raw[56:72], first, last, table, entry_size))
        if headers[0] != headers[1]:
            raise ValueError('GPT copies disagree')
        _, first, last, table, entry_size = headers[0]
        parts = []
        for number, start in enumerate(range(0, len(table), entry_size), 1):
            entry = table[start:start+entry_size]
            if entry[:16] == b'\0' * 16:
                continue
            a, b, attrs = struct.unpack_from('<QQQ', entry, 32)
            if not first <= a <= b <= last:
                raise ValueError('GPT partition outside usable space')
            parts.append({'number': number, 'start_sector': a, 'sector_count': b-a+1,
                          'type_guid': str(uuid.UUID(bytes_le=entry[:16])),
                          'unique_guid': str(uuid.UUID(bytes_le=entry[16:32])), 'attributes': attrs})
        expected = [p for p in layout['partitions'] if p['geometry'] == 'fixed']
        if (len(parts) != len(expected) or len({p['unique_guid'] for p in parts}) != len(parts)
                or any(p['unique_guid'] == str(uuid.UUID(int=0)) for p in parts)):
            raise ValueError('Unexpected source partitions')
        for actual, wanted in zip(parts, expected, strict=True):
            if any(actual[k] != wanted[k] for k in ('number', 'start_sector', 'sector_count', 'type_guid')):
                raise ValueError('Image does not match fixed update layout')
            actual['role'] = wanted['role']
        return parts


def image_records(path, parts, expected):
    """Bind the whole image and every partition in one readback pass."""
    hashes={p['role']: hashlib.sha256() for p in parts}
    whole=hashlib.sha256();offset=0
    with ordinary(path).open('rb') as stream:
        while block:=stream.read(CHUNK):
            whole.update(block);view=memoryview(block)
            for part in parts:
                start=part['start_sector']*512;end=start+part['sector_count']*512
                left=max(start,offset);right=min(end,offset+len(block))
                if left<right:hashes[part['role']].update(view[left-offset:right-offset])
            offset+=len(block)
    if whole.hexdigest()!=expected:raise ValueError('Image readback SHA mismatch')
    return {p['role']:{'role':p['role'],'bytes':p['sector_count']*512,
            'sha256':hashes[p['role']].hexdigest()} for p in parts}


class Fat:
    """Bounded FAT32 reader; never writes a FAT or follows a host path."""
    def __init__(self, path, label):
        self.path = ordinary(path)
        self.size = self.path.stat().st_size
        self.stream = self.path.open('rb')
        try:
            boot = self.read(0, 512)
            self.sector = struct.unpack_from('<H', boot, 11)[0]
            self.cluster_sectors = boot[13]
            reserved = struct.unpack_from('<H', boot, 14)[0]
            copies = boot[16]
            total, fat_size = struct.unpack_from('<II', boot, 32)
            self.root = struct.unpack_from('<I', boot, 44)[0]
            if (boot[510:] != b'\x55\xaa' or self.sector != 512 or self.cluster_sectors not in (1,2,4,8,16,32,64,128)
                    or copies != 2 or reserved < 2 or not fat_size or total * 512 > self.path.stat().st_size
                    or struct.unpack_from('<H', boot, 17)[0] != 0 or struct.unpack_from('<H', boot, 22)[0] != 0
                    or boot[71:82].decode('ascii').rstrip() != label):
                raise ValueError('Invalid FAT32 volume geometry/label')
            self.start = (reserved + copies * fat_size) * 512
            self.cluster_bytes = self.cluster_sectors * 512
            self.count = (total - reserved - copies * fat_size) // self.cluster_sectors
            if not 65525 <= self.count < 0x0ffffff5 or fat_size * 512 < (self.count + 2) * 4:
                raise ValueError('Invalid FAT32 cluster count')
            self.table = self.read(reserved * 512, fat_size * 512)
            if self.table != self.read((reserved + fat_size) * 512, fat_size * 512):
                raise ValueError('FAT copies disagree')
            backup = struct.unpack_from('<H', boot, 50)[0]
            if not 0 < backup < reserved or self.read(backup * 512, 512) != boot:
                raise ValueError('FAT boot copies disagree')
            self.uuid = f'{struct.unpack_from("<I", boot, 67)[0]:08X}'
            self.owned = set()
            self.files = {}; self.directories = []
            self.walk(self.root, '', 0)
        except BaseException:
            self.stream.close()
            raise

    def close(self):
        self.stream.close()

    def read(self, offset, size):
        if offset < 0 or size < 0 or offset + size > self.size:
            raise ValueError('FAT read outside image')
        self.stream.seek(offset); value = self.stream.read(size)
        if len(value) != size:
            raise ValueError('Truncated FAT data')
        return value

    def chain(self, first):
        result = []
        while first < 0x0ffffff8:
            if not 2 <= first < self.count + 2 or first in self.owned:
                raise ValueError('Invalid, cyclic or cross-linked FAT chain')
            self.owned.add(first); result.append(first)
            first = struct.unpack_from('<I', self.table, first * 4)[0] & 0x0fffffff
        return result

    def walk(self, cluster, prefix, depth):
        if depth > 32 or len(self.files) + len(self.directories) > 10000:
            raise ValueError('FAT inventory exceeds limits')
        long = []; expected = 0; checksum = None
        chain = self.chain(cluster)
        if len(chain) * self.cluster_bytes > 8 * 1024**2:
            raise ValueError('FAT directory exceeds limit')
        directory = b''.join(self.read(self.start + (c-2) * self.cluster_bytes, self.cluster_bytes) for c in chain)
        seen = set()
        for offset in range(0, len(directory), 32):
            entry = directory[offset:offset+32]
            if entry[0] == 0:
                break
            if entry[0] == 0xe5:
                long = []; expected = 0; continue
            attrs = entry[11]
            if attrs == 15:
                ordinal = entry[0] & 31
                if entry[0] & 64:
                    if not 1 <= ordinal <= 20:
                        raise ValueError('Invalid FAT long filename')
                    long = [None] * ordinal; expected = ordinal; checksum = entry[13]
                if not expected or ordinal != expected or entry[13] != checksum or entry[12] or entry[26:28] != b'\0\0':
                    raise ValueError('Broken FAT long filename sequence')
                long[ordinal-1] = entry[1:11] + entry[14:26] + entry[28:32]
                expected -= 1; continue
            alias = bytearray(entry[:11]); alias[0] = 0xe5 if alias[0] == 5 else alias[0]
            if long:
                value = 0
                for byte in entry[:11]: value = (((value & 1) << 7) + (value >> 1) + byte) & 255
                if expected or value != checksum:
                    raise ValueError('FAT long filename checksum mismatch')
                name = b''.join(long).decode('utf-16le').split('\0', 1)[0].rstrip('\uffff')
            else:
                stem = alias[:8].decode('cp437').rstrip(); suffix = alias[8:].decode('cp437').rstrip()
                if entry[12] & 8: stem = stem.lower()
                if entry[12] & 16: suffix = suffix.lower()
                name = stem + ('.' + suffix if suffix else '')
            long = []; expected = 0
            if attrs & 8 or name in ('.', '..'):
                continue
            folded=unicodedata.normalize('NFC',name).casefold()
            if not name or any(c in name for c in '/\\\0') or folded in seen or attrs & 0xc0:
                raise ValueError('Unsafe or colliding FAT name')
            seen.add(folded)
            if len(self.files)+len(self.directories)>=10000:raise ValueError('FAT inventory limit')
            path = prefix + name
            first = struct.unpack_from('<H', entry, 26)[0] | struct.unpack_from('<H', entry, 20)[0] << 16
            size = struct.unpack_from('<I', entry, 28)[0]
            if attrs & 16:
                if size:
                    raise ValueError('Invalid FAT directory size')
                self.directories.append(path); self.walk(first, path + '/', depth + 1)
            else:
                blocks = self.chain(first) if first else []
                if len(blocks) != (size + self.cluster_bytes - 1) // self.cluster_bytes:
                    raise ValueError('FAT file size/chain mismatch')
                self.files[path] = (size, blocks)

    def chunks(self, path):
        size, blocks = self.files[path]
        for cluster in blocks:
            value = self.read(self.start + (cluster-2) * self.cluster_bytes, min(size, self.cluster_bytes))
            size -= len(value); yield value

    def bytes(self, path, limit=1024**2):
        if self.files[path][0] > limit:
            raise ValueError('FAT metadata exceeds limit')
        return b''.join(self.chunks(path))

    def record(self, path):
        h = hashlib.sha256()
        for block in self.chunks(path): h.update(block)
        return {'path': path, 'bytes': self.files[path][0], 'sha256': h.hexdigest()}


class Tools:
    def __init__(self, sysroot, work, inputs=None):
        self.sysroot = ordinary(sysroot, True); self.work = ordinary(work, True)
        (work / 'out').mkdir(exist_ok=False)
        self.inputs=inputs or {}
        for name,path in self.inputs.items():
            ordinary(path)
            if '/' in name or name not in ('efi.img','usb-data.img','usb-logs.img','fedora-live.img'):
                raise ValueError('Unexpected inspection input role')
            (work/name).touch(exist_ok=False)

    def command(self, *argv):
        mounts=[value for name,path in self.inputs.items() for value in ('--ro-bind',str(path),'/work/'+name)]
        return ['bwrap', '--unshare-all', '--ro-bind', str(self.sysroot), '/',
                '--ro-bind', str(self.work), '/work', '--bind', str(self.work / 'out'), '/out',
                *mounts,
                '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp', '--tmpfs', '/run',
                '--clearenv', '--setenv', 'PATH', '/usr/sbin:/usr/bin:/sbin:/bin', '--setenv', 'LC_ALL', 'C', '--', *argv]

    def run(self, *argv):
        with tempfile.TemporaryFile() as out,tempfile.TemporaryFile() as error:
            result=subprocess.run(self.command(*argv),stdout=out,stderr=error,timeout=180)
            if out.tell()>1024**2 or error.tell()>1024**2:raise ValueError('Inspection command metadata exceeds limit')
            out.seek(0);error.seek(0);data=out.read();details=error.read()
            if result.returncode:
                raise ValueError('Read-only inspection failed: '+data.decode(errors='replace')+details.decode(errors='replace'))
            return data

    def cat(self, image, path, target=None):
        if not re.fullmatch(r'/[A-Za-z0-9_./+-]+', path) or '..' in path.split('/'):
            raise ValueError('Unsafe debugfs read path')
        if target is not None:
            if target.exists(): raise ValueError('Fresh extraction file required')
            self.run('/usr/sbin/debugfs', '-R', f'dump {path} /out/{target.name}', image)
            return ordinary(target)
        data = self.run('/usr/sbin/debugfs', '-R', 'cat ' + path, image)
        if len(data) > 1024**2: raise ValueError('Ext4 metadata exceeds limit')
        return data

    def inventory(self, image):
        records = []; directories = ['/']; visited = set();logical_bytes=0
        while directories:
            directory = directories.pop()
            if directory in visited or len(visited) + len(records) > 10000:
                raise ValueError('Ext4 directory cycle/inventory limit')
            visited.add(directory)
            listing = self.run('/usr/sbin/debugfs', '-R', 'ls -p ' + directory, image).decode()
            for line in listing.splitlines():
                if not line: continue
                fields = line.split('/')
                if len(fields) != 8 or not fields[1].isdigit():
                    raise ValueError('Unexpected debugfs directory entry')
                if fields[1] == '0' or fields[5] in ('.', '..'): continue
                mode = int(fields[2], 8); name = fields[5]
                if not re.fullmatch(r'[A-Za-z0-9_.+-]+', name) or name in ('.', '..'):
                    raise ValueError('Unsafe ext4 logical name')
                path = directory + name
                if stat.S_ISDIR(mode):
                    directories.append(path + '/'); continue
                if not stat.S_ISREG(mode): raise ValueError('Nonregular MASTER logical file')
                size = int(fields[6]); h = hashlib.sha256(); count = 0
                logical_bytes+=size
                if logical_bytes>64*1024**3:raise ValueError('Logical MASTER inventory exceeds producer inspection limit')
                with subprocess.Popen(self.command('/usr/sbin/debugfs', '-R', 'cat ' + path, image),
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
                    while block := process.stdout.read(CHUNK):
                        count += len(block); h.update(block)
                        if count>size:
                            process.kill();raise ValueError('Ext4 file exceeds listed size')
                    if process.wait() or count != size: raise ValueError('Ext4 readback size mismatch')
                records.append({'path': path.lstrip('/'), 'bytes': size, 'sha256': h.hexdigest()})
        return sorted(records, key=lambda item: item['path'])
