#!/usr/bin/env python3
"""Restore the appliance's fixed, uncompressed initramfs into RAM only."""
import json
import os
from pathlib import Path
import subprocess
import sys


def archive_names(image):
    with image.open('rb') as stream:
        if stream.read(6) != b'070701':
            raise ValueError('Expected the appliance uncompressed newc initramfs')
    with image.open('rb') as stream:
        result = subprocess.run(['cpio', '-it', '--quiet'], stdin=stream,
                                capture_output=True, text=True, check=True)
    names = {name.removeprefix('./') for name in result.stdout.splitlines()}
    if any(name.startswith('/') or '..' in Path(name).parts for name in names):
        raise ValueError('Unsafe initramfs archive path')
    required = {'shutdown', 'usr/bin/bash', 'usr/bin/python3.13',
                'var/lib/dracut/hooks/shutdown/10-shutdown-root.sh'}
    if not required <= names:
        raise ValueError('Initramfs lacks shutdown runtime or storage hook')


def prepare(image, destination):
    archive_names(image)
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with image.open('rb') as stream:
            subprocess.run(['cpio', '-idm', '--no-absolute-filenames', '--quiet'],
                           stdin=stream, cwd=destination, check=True)
        subprocess.run(['chroot', str(destination), '/bin/sh', '-n', '/shutdown'], check=True)
        subprocess.run(['chroot', str(destination), '/bin/sh', '-c',
                        'test -x /shutdown && test -r /var/lib/dracut/hooks/shutdown/10-shutdown-root.sh'], check=True)
        subprocess.run(['chroot', str(destination), '/usr/bin/python3', '-c',
                        'import ctypes,json; ctypes.CDLL("libkmod.so.2")'], check=True)
        (destination / '.need_shutdown').unlink(missing_ok=True)
    except Exception:
        (destination / 'shutdown').unlink(missing_ok=True)
        raise


if __name__ == '__main__':
    try:
        if not Path('/run/gpu-root/state.json').is_file():
            raise ValueError('Managed writable root state required')
        image = Path('/boot') / ('initramfs-' + os.uname().release + '.img')
        prepare(image, Path('/run/initramfs'))
        Path('/run/gpu-root/shutdown-ready.json').write_text(json.dumps({'ready': True, 'image': str(image)}) + '\n')
        print('GPU-ROOT: RAM shutdown runtime restored and checked', flush=True)
    except Exception as error:
        sys.exit('GPU-ROOT SHUTDOWN PREPARE STOP: ' + str(error))
