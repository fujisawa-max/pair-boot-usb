#!/usr/bin/env python3
"""Record an actual Git base and explicitly selected public build sources."""
import argparse
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--repository', type=Path, default=ROOT)
    parser.add_argument('--commit')
    parser.add_argument('--source-hashes', type=Path, help='explicit historical path-to-SHA256 snapshot')
    parser.add_argument('--source-extra', action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    bundle = runpy.run_path(str(ROOT / 'scripts/prepare-update-bundle.py'))
    fresh = runpy.run_path(str(ROOT / 'scripts/prepare-firmware.py'))['fresh_output']
    output = fresh(args.output)
    hashes = bundle['read_json'](args.source_hashes) if args.source_hashes else None
    source = runpy.run_path(str(ROOT / 'scripts/updater-source.py'))['provenance'](
        args.source_root, args.repository, args.commit, hashes, args.source_extra)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream: stream.write(bundle['canonical'](source))
    print('Recorded actual public build-source inventory:', output)


if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, KeyError) as error: sys.exit(f'Source recording stopped: {error}')
