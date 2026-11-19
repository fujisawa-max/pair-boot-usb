#!/usr/bin/env python3
"""Generate pinned gpu-unlock firmware in build/ without installing it."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def fresh_output(raw):
    path = raw.absolute()
    if '..' in path.parts or not path.is_relative_to(ROOT / 'build') or path == ROOT / 'build':
        raise ValueError('Fresh output below build/ required')
    for item in (path, *path.parents):
        if item == ROOT:
            break
        if item.is_symlink():
            raise ValueError('Refusing symlink output path')
    if path.exists():
        raise ValueError('Output already exists; preserve it and choose a fresh path')
    return path


def read_source_members(archive_path, expected):
    results = {}
    with tarfile.open(archive_path, 'r:xz') as archive:
        for member in archive:
            if member.name not in expected:
                continue
            if member.name in results or not member.isfile() or not 0 < member.size <= 32 * 1024 * 1024:
                raise ValueError('Invalid or duplicate firmware source member')
            results[member.name] = archive.extractfile(member).read()
    if set(results) != set(expected):
        raise ValueError('Missing pinned firmware source members')
    return results


def read_installer_members(installer):
    """Read a makeself zstd/tar payload as data; never execute its shell header."""
    with installer.open('rb') as stream:
        header = stream.read(1024 * 1024)
        versions = re.findall(rb'^version_string=([0-9.]+)$', header, re.M)
        skips = re.findall(rb'^skip=(\d+)$', header, re.M)
        if versions != [b'610.43.03'] or len(skips) != 1 or not 1 < int(skips[0]) <= 100000:
            raise ValueError('Invalid or wrong-version NVIDIA installer header')
        lines = header.split(b'\n')
        skip = int(skips[0]) - 1
        if len(lines) <= skip:
            raise ValueError('Incomplete NVIDIA installer header')
        stream.seek(sum(len(line) + 1 for line in lines[:skip]))
        process = subprocess.Popen(['zstd', '-dc'], stdin=stream, stdout=subprocess.PIPE)
        expected = {'firmware/gsp_ga10x.bin', 'firmware/gsp_tu10x.bin', 'LICENSE'}
        result = {}
        try:
            with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
                for member in archive:
                    if member.name not in expected:
                        continue
                    if member.name in result or not member.isfile() or not 0 < member.size <= 128 * 1024 * 1024:
                        raise ValueError('Invalid or duplicate GSP installer member')
                    result[member.name] = archive.extractfile(member).read()
            # Drain bounded tar padding before waiting for the decompressor.
            if len(process.stdout.read(1024 * 1024 + 1)) > 1024 * 1024:
                raise ValueError('Excessive installer archive padding')
            if process.wait() != 0 or set(result) != expected:
                raise ValueError('Missing GSP firmware or invalid compressed installer')
            return {('NVIDIA-LICENSE' if name == 'LICENSE' else Path(name).name): data
                    for name, data in result.items()}
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/assets-gpu-unlock-2.0.0')
    args = parser.parse_args()
    output = fresh_output(args.output)
    upstream = runpy.run_path(str(ROOT / 'scripts/prepare-upstream.py'))
    source_lock = upstream['source_lock']()
    upstream['verify'](ROOT / source_lock['directory'], source_lock['commit'])
    fetch = runpy.run_path(str(ROOT / 'scripts/fetch-inputs.py'))
    inputs = fetch['input_plan'](['firmware'])
    for entry in inputs:
        fetch['fetch'](entry, offline=True)
    source, package = [ROOT / entry['file'] for entry in inputs]
    code = ROOT / source_lock['directory'] / 'firmware'
    pinned = json.loads((ROOT / 'inputs/firmware-output.json').read_text())
    if pinned['upstream_commit'] != source_lock['commit']:
        raise ValueError('Firmware output lock differs from upstream commit')
    extractor = runpy.run_path(str(code / 'extract_boot_assets.py'))
    prefix = 'NVIDIA-kernel-module-source-610.43.03/src/nvidia/generated/'
    expected = {prefix + name for name in extractor['SOURCES']}
    members = read_source_members(source, expected)
    gsp = read_installer_members(package)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='firmware-source-', dir=ROOT / 'build') as temporary:
        source_root = Path(temporary)
        for name, data in members.items():
            target = source_root / 'src/nvidia/generated' / Path(name).name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        extractor['extract_assets'](source_root, output)
    for name, data in gsp.items():
        (output / name).write_bytes(data)
    hashes = pinned['files']
    if {path.name for path in output.iterdir()} != set(hashes):
        raise ValueError('Unexpected generated firmware file inventory')
    for name, entry in hashes.items():
        with (output / name).open('rb') as stream:
            if (output / name).stat().st_size != entry['size'] or hashlib.file_digest(stream, 'sha256').hexdigest() != entry['sha256']:
                raise ValueError(f'Generated firmware hash mismatch: {name}')
    (output / 'firmware-verification.json').write_text(json.dumps({
        'schema': 1, 'inputs': inputs, 'files': hashes, 'installed_on_host': False,
        'hardware_tested': False}, indent=2) + '\n')
    print(f'Generated and verified {len(hashes)} firmware/notice files in {output}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.CalledProcessError) as error:
        sys.exit(f'Firmware preparation failed: {error}')
