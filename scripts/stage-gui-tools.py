#!/usr/bin/env python3
"""Stage connected GUIs, public trust pin and a Fedora RAM-overlay launcher hook."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def cpio(files):
    archive = bytearray()
    for index, (name, content, mode) in enumerate([*files, ('TRAILER!!!', b'', 0)]):
        encoded = name.encode() + b'\0'
        fields = (index + 1, mode, 0, 0, 1, 0, len(content), 0, 0, 0, 0, len(encoded), 0)
        archive += b'070701' + ''.join(f'{field:08x}' for field in fields).encode() + encoded
        archive += b'\0' * ((-len(archive)) % 4)
        archive += content
        archive += b'\0' * ((-len(archive)) % 4)
    return bytes(archive)


def stage(config, fedora, certificate):
    for path in (config, fedora):
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT / 'build') or not path.is_dir():
            raise ValueError('Generated build/ staging directories required')
    if certificate.is_symlink() or not certificate.is_file():
        raise ValueError('Ordinary public DER certificate required')
    subprocess.run(['openssl', 'x509', '-inform', 'DER', '-in', str(certificate), '-noout'], check=True, capture_output=True)
    pin = {'schema': 1, 'certificate_sha256': hashlib.sha256(certificate.read_bytes()).hexdigest()}
    for tree in (config, fedora):
        gui = tree / 'gui'
        if gui.exists():
            raise ValueError('GUI staging must be fresh')
        shutil.copytree(ROOT / 'gui', gui, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'operation-plan.py'))
        (gui / 'trust.json').write_text(json.dumps(pin, indent=2) + '\n')
        for path in gui.glob('*.sh'):
            subprocess.run(['sh', '-n', str(path)], check=True)
        (tree / 'start-windows-gui-debug.cmd').write_bytes(b'@echo off\r\ncall "%~dp0gui\\start-windows-gui-debug.cmd"\r\n')
        (tree / 'start-windows-gui.cmd').write_bytes(b'@echo off\r\ncall "%~dp0gui\\start-windows-gui.cmd"\r\n')
        (tree / 'start-fedora-gui.sh').write_text('#!/bin/sh\nexec /usr/bin/bash "$(dirname -- "$0")/gui/start-fedora-gui.sh"\n')
        shutil.copy2(ROOT / 'gui/gpu-linux.desktop', tree / 'gpu-linux.desktop')
    files = [
        ('var/lib/dracut/hooks/pre-pivot/99-gpu-gui-launchers.sh', (ROOT / 'gui/fedora-launchers-hook.sh').read_bytes(), stat.S_IFREG | 0o755),
        ('usr/lib/gpu-linux-launchers-runtime.sh', (ROOT / 'gui/fedora-launchers-runtime.sh').read_bytes(), stat.S_IFREG | 0o755),
        ('usr/lib/gpu-linux-autostart.desktop', (ROOT / 'gui/gpu-linux-autostart.desktop').read_bytes(), stat.S_IFREG | 0o644),
        ('usr/lib/gpu-linux.desktop', (ROOT / 'gui/gpu-linux.desktop').read_bytes(), stat.S_IFREG | 0o644),
    ]
    archive = cpio(files)
    target = fedora / 'boot/x86_64/loader/initrd'
    if target.is_symlink() or not target.is_file():
        raise ValueError('Missing Fedora initrd')
    original_size = target.stat().st_size
    original_sha = hashlib.sha256(target.read_bytes()).hexdigest()
    with target.open('ab') as stream:
        stream.write(b'\0' * ((-stream.tell()) % 4))
        stream.write(archive)
    (fedora / 'gui-launcher-overlay.json').write_text(json.dumps({
        'schema': 1, 'original_initrd_sha256': original_sha, 'original_initrd_bytes': original_size,
        'append_cpio_sha256': hashlib.sha256(archive).hexdigest(),
        'files': [item[0] for item in files], 'autostart': True,
        'modifies': 'Fedora Live RAM overlay only; no NTFS or firmware access',
        'native_gui_tested': False}, indent=2) + '\n')
    return pin


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-tree', type=Path, required=True)
    parser.add_argument('--fedora-tree', type=Path, required=True)
    parser.add_argument('--certificate', type=Path, required=True)
    args = parser.parse_args()
    stage(args.config_tree, args.fedora_tree, args.certificate)
