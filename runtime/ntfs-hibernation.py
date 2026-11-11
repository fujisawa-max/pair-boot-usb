#!/usr/bin/env python3
"""Inspect NTFS hibernation metadata through the pinned library, read-only."""
import argparse
import ctypes
import errno
import json
import os
import stat

LIBRARY = '/usr/lib/x86_64-linux-gnu/libntfs-3g.so.89'
# ntfs-3g 2022.10.3 include/ntfs-3g/volume.h. No recovery or write flags.
READ_ONLY_FORENSIC = 0x00000001 | 0x04000000


def probe(path):
    library = ctypes.CDLL(LIBRARY, use_errno=True)
    library.ntfs_mount.argtypes = (ctypes.c_char_p, ctypes.c_ulong)
    library.ntfs_mount.restype = ctypes.c_void_p
    library.ntfs_volume_check_hiberfile.argtypes = (ctypes.c_void_p, ctypes.c_int)
    library.ntfs_volume_check_hiberfile.restype = ctypes.c_int
    library.ntfs_umount.argtypes = (ctypes.c_void_p, ctypes.c_int)
    library.ntfs_umount.restype = ctypes.c_int
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        mode = os.fstat(fd).st_mode
        if not (stat.S_ISBLK(mode) or stat.S_ISREG(mode)):
            raise ValueError('Expected a block device or ordinary image file')
        # Keep inspecting the opened object even if device paths change.
        volume = library.ntfs_mount(f'/proc/self/fd/{fd}'.encode(), READ_ONLY_FORENSIC)
        if not volume:
            return {'schema': 1, 'status': 'unknown', 'reason': 'ntfs-open-failed'}
        try:
            result = library.ntfs_volume_check_hiberfile(volume, 0)
            error = ctypes.get_errno()
        finally:
            closed = library.ntfs_umount(volume, 0)
        if result < 0 and error == errno.EPERM:
            return {'schema': 1, 'status': 'hibernated'}
        if result != 0 or closed != 0:
            return {'schema': 1, 'status': 'unknown', 'reason': 'ntfs-inspection-failed'}
        return {'schema': 1, 'status': 'not-detected'}
    finally:
        os.close(fd)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('device')
    args = parser.parse_args()
    try:
        result = probe(args.device)
    except (OSError, ValueError) as error:
        result = {'schema': 1, 'status': 'unknown', 'reason': type(error).__name__}
    print(json.dumps(result), flush=True)
