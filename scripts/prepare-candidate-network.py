#!/usr/bin/env python3
"""Extract hash-locked network data offline; never configure host packages."""
import argparse
import json
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'build/candidate-network-overlay-v1')
    args = parser.parse_args()
    output = args.output.absolute()
    build = ROOT / 'build'
    if (not output.is_relative_to(build) or '..' in output.parts or
            output == build or output.exists()):
        raise ValueError('Output must be a fresh directory below build/')
    for item in (output, *output.parents):
        if item == ROOT:
            break
        if item.is_symlink():
            raise ValueError('Refusing symlink output path')
    fetcher = runpy.run_path(str(ROOT / 'scripts/fetch-inputs.py'))
    entries = fetcher['input_plan'](['network'])
    # Verify every input before creating or extracting any output.
    for entry in entries:
        fetcher['fetch'](entry, offline=True)
    output.mkdir(parents=True)
    for entry in entries:
        subprocess.run(['dpkg-deb', '--extract', str(ROOT / entry['file']), str(output)], check=True)
    lock = json.loads((ROOT / 'inputs/candidate-network-inputs.json').read_text())
    (output / 'candidate-network-inputs.json').write_text(json.dumps(lock, indent=2) + '\n')
    print(f'Verified and extracted {len(entries)} network packages: {output}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f'Network preparation failed: {error}')
