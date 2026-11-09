#!/usr/bin/env python3
"""Detach one recorded loop, confirming that it is no longer configured."""
import argparse
import errno
import fcntl
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

# Linux UAPI: include/uapi/linux/loop.h, struct loop_info64 (232 bytes).
LOOP_GET_STATUS64 = 0x4C05


def check_node(info, number):
    if not stat.S_ISBLK(info.st_mode) or os.major(info.st_rdev) != 7 or os.minor(info.st_rdev) != number:
        raise ValueError('Recorded loop path is not the expected loop block device')


def attached(device):
    match = re.fullmatch(r'/dev/loop([0-9]+)', str(device))
    if match is None:
        raise ValueError('Invalid recorded loop path')
    number = int(match[1])
    check_node(Path(device).lstat(), number)
    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as error:
        if error.errno == errno.ENXIO:
            return False
        raise
    try:
        check_node(os.fstat(fd), number)
        try:
            fcntl.ioctl(fd, LOOP_GET_STATUS64, bytearray(232), True)
        except OSError as error:
            if error.errno == errno.ENXIO:
                return False
            raise
        return True
    finally:
        os.close(fd)


def detach(device, timeout=2.0):
    if not attached(device):
        return
    # The status probe closes its fd before detach, so it cannot hold autoclear open.
    result = subprocess.run(['losetup', '--detach', str(device)],
                            capture_output=True, text=True, timeout=5)
    deadline = time.monotonic() + timeout
    while attached(device):
        if result.returncode:
            raise RuntimeError('Loop detach failed while still configured: ' + str(device))
        if time.monotonic() >= deadline:
            raise RuntimeError('Loop remains configured after detach request: ' + str(device))
        time.sleep(0.05)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('device')
    args = parser.parse_args()
    try:
        detach(args.device)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit('GPU-ROOT LOOP STOP: ' + str(error))
