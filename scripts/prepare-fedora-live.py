#!/usr/bin/env python3
"""Verify and extract the supplied official Fedora ISO into a workspace tree."""
import argparse
import hashlib
import io
import json
import runpy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('iso', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--offline', action='store_true', help='use cached signed CHECKSUM')
    args = parser.parse_args()
    output = runpy.run_path(str(ROOT / 'scripts/prepare-firmware.py'))['fresh_output'](args.output)
    locked = json.loads((ROOT / 'inputs/fedora-live-input.json').read_text())
    if args.iso.absolute() != ROOT / locked['archives'][0]['file']:
        parser.error('ISO must be the acquired input at the path in inputs/fedora-live-input.json')
    verification = runpy.run_path(str(ROOT / 'scripts/verify-fedora-input.py'))['verify'](args.offline)
    fetch = runpy.run_path(str(ROOT / 'scripts/fetch-inputs.py'))
    tools = fetch['input_plan'](['fedora-tools'])
    if len(tools) != 1:
        raise ValueError('Expected exactly one pinned Fedora extraction wheel')
    fetch['fetch'](tools[0], offline=True)
    sys.path.insert(0, str(ROOT / tools[0]['file']))
    import pycdlib
    iso = pycdlib.PyCdlib()
    iso.open(str(args.iso))
    output.mkdir(parents=True)
    files = []
    case_names = {}
    for folder, _, names in iso.walk(rr_path='/'):
        for name in names:
            path = Path(folder.lstrip('/')) / name
            if '..' in path.parts:
                raise ValueError('Unsafe ISO path')
            key = str(path).lower()
            # Fedora ISO has uppercase/lowercase aliases for the same EFI files.
            if key in case_names:
                original = case_names[key]
                duplicate = io.BytesIO()
                iso.get_file_from_iso_fp(duplicate, rr_path='/' + str(path))
                if hashlib.sha256(duplicate.getvalue()).hexdigest() != hashlib.sha256(original.read_bytes()).hexdigest():
                    raise ValueError('FAT case collision differs: ' + str(path))
                continue
            if key == 'efi/boot/bootx64.efi':
                path = Path('EFI/BOOT/BOOTX64.EFI')
            target = output / path
            target.parent.mkdir(parents=True, exist_ok=True)
            iso.get_file_from_iso(str(target), rr_path=folder.rstrip('/') + '/' + name)
            if target.stat().st_size >= 4 * 1024**3:
                raise ValueError('ISO payload exceeds FAT32 individual-file limit')
            case_names[key] = target
            files.append(str(path))
    iso.close()
    config = output / 'boot/grub2/grub.cfg'
    text = config.read_text()
    if 'root=live:CDLABEL=Fedora-WS-Live-44' not in text:
        raise ValueError('Unexpected Fedora boot configuration')
    text = text.replace('root=live:CDLABEL=Fedora-WS-Live-44', 'root=live:UUID=' + locked['filesystem_uuid'])
    text = text.replace('set default="1"', 'set default="0"', 1)
    # ISO media-check hashes do not describe the extracted USB filesystem.
    text = text.replace(' rd.live.check', '')
    text = text.replace('Test this media & start Fedora-Workstation-Live', 'Start Fedora-Workstation-Live (no ISO media check)')
    config.write_text(text)
    manifest = {'source_iso': args.iso.name, 'source_sha256': locked['archives'][0]['sha256'],
                'upstream_verification': verification,
                'filesystem_uuid': locked['filesystem_uuid'], 'fat_id': locked['fat_id'], 'label': 'GPU-FEDORA',
                'files': files, 'payload_bytes': sum(p.stat().st_size for p in output.rglob('*') if p.is_file()),
                'modifications': ['live root uses FAT UUID', 'default normal Live start', 'ISO-only media check disabled'],
                'native_secure_boot_chain_tested': False}
    (output / 'fedora-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({k:v for k,v in manifest.items() if k != 'files'}, indent=2))


if __name__ == '__main__':
    main()
