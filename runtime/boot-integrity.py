#!/usr/bin/env python3
"""Verify authorization anchored to the release signer embedded in the initramfs."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import tempfile

SIGNER = Path('/usr/lib/pair-boot-integrity-signer.pem')
MANIFEST = 'gpu-linux/boot-integrity.json'
SIGNATURE = 'gpu-linux/boot-integrity.sig'
LIMIT = 64 * 1024
VERITY_FILES = ('gpu-linux/root.verity', 'gpu-linux/root.verity.json')
VERITY_SUPERBLOCK = struct.Struct('<8sII16s32sIIQH6x256s')


def open_file(path):
    path = Path(path)
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('Symlink boot artifact')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    stream = os.fdopen(fd, 'rb')
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        stream.close()
        raise ValueError('Expected an ordinary boot artifact')
    return stream


def bounded(path):
    with open_file(path) as stream:
        data = stream.read(LIMIT + 1)
    if not 0 < len(data) <= LIMIT:
        raise ValueError('Invalid boot metadata size')
    return data


def verify_installed_root(image, metadata, progress=None):
    """Check the selected on-disk root against authenticated MASTER metadata."""
    with open_file(image) as stream:
        before = os.fstat(stream.fileno())
        if before.st_size != metadata['size']:
            raise ValueError('Installed root size differs from the signed MASTER')
        digest = hashlib.sha256()
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
            if progress is not None:
                progress.advance(len(chunk))
        after = os.fstat(stream.fileno())
        if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or digest.hexdigest() != metadata['sha256']):
            raise ValueError('Installed root SHA-256 differs from the signed MASTER')


def signer_pem(path):
    pem = bounded(path)
    if not re.fullmatch(rb'-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----',
                        pem.strip()):
        raise ValueError('Expected exactly one pinned signer certificate in PEM')
    return pem


def verify_signature(payload, detached, pem):
    with tempfile.TemporaryDirectory(prefix='pair-boot-integrity-') as folder:
        work = Path(folder)
        (work / 'signer.pem').write_bytes(pem)
        (work / 'manifest.json').write_bytes(payload)
        (work / 'signature.der').write_bytes(detached)
        subprocess.run(
            ['openssl', 'cms', '-verify', '-binary', '-inform', 'DER', '-verify_retcode',
             '-in', 'signature.der', '-content', 'manifest.json', '-certfile', 'signer.pem',
             '-nointern', '-noverify', '-out', os.devnull],
            cwd=work, capture_output=True, check=True, timeout=15,
        )


def unique_fields(items):
    value = {}
    for key, entry in items:
        if key in value:
            raise ValueError('Duplicate boot authorization field')
        value[key] = entry
    return value


def verity_parameters(tree, authorized, root, destination=None):
    present = [name in authorized for name in VERITY_FILES]
    staged = [os.path.lexists(tree / name) for name in VERITY_FILES]
    if not any(present + staged):
        return None
    if not all(present + staged):
        raise ValueError('Incomplete or unsigned verity artifacts')
    descriptor = bounded(tree / VERITY_FILES[1])
    if authorized[VERITY_FILES[1]] != {'bytes': len(descriptor),
                                      'sha256': hashlib.sha256(descriptor).hexdigest()}:
        raise ValueError('Signed verity descriptor differs')
    value = json.loads(descriptor, object_pairs_hook=unique_fields)
    hashes = authorized[VERITY_FILES[0]]
    if (not isinstance(value, dict) or type(value.get('schema')) is not int or value['schema'] != 1
            or value.get('root') != root or value.get('hash_image') != hashes
            or not isinstance(value.get('root_hash'), str)
            or not re.fullmatch('[0-9a-f]{64}', value['root_hash'])
            or not isinstance(hashes, dict) or type(hashes.get('bytes')) is not int
            or not 8192 <= hashes['bytes'] <= 128 * 1024**2):
        raise ValueError('Invalid signed verity descriptor')
    with open_file(tree / VERITY_FILES[0]) as stream:
        data = stream.read(hashes['bytes'] + 1)
    if hashes != {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}:
        raise ValueError('Signed verity hash image differs')
    magic, version, hash_type, _, algorithm, block, hash_block, blocks, salt_size, salt = (
        VERITY_SUPERBLOCK.unpack_from(data))
    if (magic != b'verity\0\0' or version != 1 or hash_type != 1
            or algorithm.rstrip(b'\0') != b'sha256' or block != 4096 or hash_block != 4096
            or blocks * block != root['bytes'] or not 0 <= salt_size <= len(salt)
            or len(data) % hash_block):
        raise ValueError('Unsupported verity superblock')
    if destination is not None:
        with destination.open('xb') as output:
            output.write(data)
    return blocks, value['root_hash'], salt[:salt_size].hex() or '-'


def verity_table(parameters, data_loop, hash_loop):
    blocks, root_hash, salt = parameters
    return f'0 {blocks * 8} verity 1 {data_loop} {hash_loop} 4096 4096 {blocks} 1 sha256 {root_hash} {salt}'


def verify_master(tree, signer=SIGNER, *, verity_copy=None, verity_result=None):
    """Return authenticated master metadata bytes, or None on an unenrolled build."""
    tree = Path(tree)
    if not signer.exists() and not signer.is_symlink():
        if any(path.exists() or path.is_symlink() for path in
               (tree / MANIFEST, tree / SIGNATURE, *(tree / name for name in VERITY_FILES))):
            raise ValueError('Boot authorization has no embedded release signer')
        return None
    for parent in (tree, tree / 'gpu-linux', tree / 'boot', tree / 'boot/grub'):
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError('Invalid MASTER directory')
    payload = bounded(tree / MANIFEST)
    pem = signer_pem(signer)
    verify_signature(payload, bounded(tree / SIGNATURE), pem)
    value = json.loads(payload, object_pairs_hook=unique_fields)
    if (not isinstance(value, dict) or type(value.get('schema')) is not int
            or value['schema'] != 1 or value.get('kind') != 'pair-boot-integrity'
            or value.get('profile') not in ('standard', 'loan')
            or not isinstance(value.get('files'), dict)):
        raise ValueError('Invalid signed boot authorization')
    authorized = value['files']
    master = bounded(tree / 'gpu-linux/master.json')
    metadata = json.loads(master, object_pairs_hook=unique_fields)
    if (not isinstance(metadata, dict) or metadata.get('version') != value.get('version')
            or metadata.get('secure_boot_signed_artifacts') is not True
            or metadata.get('boot_integrity_signer_sha256') != hashlib.sha256(pem).hexdigest()
            or authorized.get('gpu-linux/root.img') !=
            {'bytes': metadata.get('size'), 'sha256': metadata.get('sha256')}):
        raise ValueError('Signed root record does not match MASTER metadata')
    for relative in ('gpu-linux/master.json', 'boot/grub/grub.cfg', 'boot/vmlinuz', 'boot/initramfs.img'):
        with open_file(tree / relative) as stream:
            if relative == 'gpu-linux/master.json':
                length, digest = len(master), hashlib.sha256(master).hexdigest()
            else:
                length = os.fstat(stream.fileno()).st_size
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if authorized.get(relative) != {'bytes': length, 'sha256': digest}:
            raise ValueError('Signed MASTER artifact differs: ' + relative)
    verity = verity_parameters(tree, authorized, authorized['gpu-linux/root.img'], verity_copy)
    if verity_result is not None and verity is not None:
        verity_result.append(verity)
    # gpu-root checks actual root/state bytes against this authenticated
    # metadata before opening the NTFS backing volume for write access.
    return master
