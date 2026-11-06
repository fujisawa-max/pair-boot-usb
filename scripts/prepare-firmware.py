#!/usr/bin/env python3
"""Generate pinned gpu-unlock firmware in build/ without installing it."""
import argparse
import hashlib
import json
from pathlib import Path
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/assets')
    args = parser.parse_args()
    output = fresh_output(args.output)
    upstream = runpy.run_path(str(ROOT / 'scripts/prepare-upstream.py'))
    upstream['verify'](ROOT / 'vendor/gpu-unlock', 'af28cbbfd3d922a659227ecfb7369adfd924cf03')
    fetch = runpy.run_path(str(ROOT / 'scripts/fetch-inputs.py'))
    inputs = fetch['input_plan'](['firmware'])
    for entry in inputs:
        fetch['fetch'](entry, offline=True)
    source, package = [ROOT / entry['file'] for entry in inputs]
    code = ROOT / 'vendor/gpu-unlock/firmware'
    extractor = runpy.run_path(str(code / 'extract_boot_assets.py'))
    prefix = 'NVIDIA-kernel-module-source-610.43.03/src/nvidia/generated/'
    expected = {prefix + name for name in extractor['PINNED']}
    members = read_source_members(source, expected)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='firmware-source-', dir=ROOT / 'build') as temporary:
        source_root = Path(temporary)
        for name, data in members.items():
            target = source_root / 'src/nvidia/generated' / Path(name).name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        extractor['extract_assets'](source_root, output)
    subprocess.run([sys.executable, str(code / 'generate_v67.py'), str(package), str(output)], check=True)
    pinned = json.loads((code / 'artifacts-610.43.03.json').read_text())
    hashes = {**pinned['derived_assets'],
              'gsp_ga10x.bin': pinned['source_artifacts'][2]['firmware'][0]['sha256'],
              'v67-ga102-610.43.03.bin': pinned['generated_payload']['sha256'],
              'NVIDIA-LICENSE': pinned['source_artifacts'][2]['notice']['sha256']}
    for name, sha in hashes.items():
        with (output / name).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != sha:
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
