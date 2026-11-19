#!/usr/bin/env python3
"""Extract pinned inspection tools into a fresh workspace; never install them."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'build/updater-inspection-sysroot')
    parser.add_argument('--offline',action='store_true')
    args=parser.parse_args()
    command=[sys.executable,str(ROOT/'scripts/prepare-debian-build.py'),'--lock',
             str(ROOT/'inputs/updater-inspection-tools.json'),'--output',str(args.output)]
    if args.offline:command.append('--offline')
    subprocess.run(command,check=True)
    for name in ('dev','proc','sys','tmp','run'):(args.output/name).mkdir(exist_ok=True)
    print('Inspection sysroot prepared; commands run in a separate namespace.')


if __name__=='__main__':main()
