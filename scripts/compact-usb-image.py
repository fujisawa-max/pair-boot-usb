#!/usr/bin/env python3
"""Copy a generated regular image without trailing free space; repair file GPT."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    build = (ROOT / 'build').resolve()
    if not source.is_relative_to(build) or not source.is_file():
        parser.error('source must be a regular generated file below build/')
    if not output.is_relative_to(build) or output.exists():
        parser.error('output must be a new file below build/')
    subprocess.run(['sgdisk', '--verify', str(source)], check=True)
    table = json.loads(subprocess.check_output(['sfdisk', '--json', str(source)]))['partitiontable']
    if table['label'] != 'gpt' or table['sectorsize'] != 512:
        parser.error('expected a 512-byte sector GPT image')
    partitions = table['partitions']
    end = max(p['start'] + p['size'] for p in partitions) * 512
    size = ((end + 1024 * 1024 - 1) // (1024 * 1024) + 1) * 1024 * 1024
    if size >= source.stat().st_size:
        parser.error('no removable trailing space')
    output.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as inp, output.open('xb') as out:
        remaining = size
        while remaining:
            block = inp.read(min(8 * 1024 * 1024, remaining))
            if not block:
                raise ValueError('source truncated')
            out.write(block)
            remaining -= len(block)
    # Only the new regular file is modified; partition contents and UUIDs stay.
    subprocess.run(['sgdisk', '--move-second-header', str(output)], check=True)
    subprocess.run(['sgdisk', '--verify', str(output)], check=True)
    new = json.loads(subprocess.check_output(['sfdisk', '--json', str(output)]))['partitiontable']
    fields = ('start', 'size', 'type', 'uuid', 'name')
    assert [{k: p[k] for k in fields} for p in partitions] == [{k: p[k] for k in fields} for p in new['partitions']]
    with source.open('rb') as inp, output.open('rb') as out:
        for p in partitions:
            inp.seek(p['start'] * 512)
            out.seek(p['start'] * 512)
            remaining = p['size'] * 512
            while remaining:
                count = min(8 * 1024 * 1024, remaining)
                assert inp.read(count) == out.read(count), 'partition bytes differ'
                remaining -= count
    with output.open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    report = {'source': str(source), 'output': str(output), 'size': size, 'sha256': sha,
              'partition_bytes_identical': True, 'partition_uuids_preserved': True}
    output.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    output.with_suffix('.sha256').write_text(sha + '  ' + output.name + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
