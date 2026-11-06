#!/usr/bin/env python3
"""Stage PAIR defaults and verified runtime into a fresh generated root only."""
import hashlib
import json
from pathlib import Path
import runpy
import shutil

ROOT = Path(__file__).resolve().parents[1]
APP = Path('home/gpu/.config/Nvidia Corporation/Personal AI Router')


def seed(root):
    app = root / APP
    if app.exists():
        raise ValueError('PAIR state already exists; refusing to replace identity/settings')
    (app / 'engines').mkdir(parents=True)
    override = {'engine': 'llamacpp', 'platforms': {'linux/amd64': {'runtime': {'launch_args': ['--models-dir', '/models']}}}}
    (app / 'engines/llamacpp.json').write_text(json.dumps(override, indent=2) + '\n')
    binaries = app / 'engine-bin'
    binaries.mkdir()
    (binaries / 'engine-state.json').write_text(json.dumps({'engines': {'llamacpp': True}}, indent=2) + '\n')
    return binaries / 'llamacpp'


def stage(root):
    root = root.resolve()
    if not root.is_relative_to(ROOT / 'build') or root == ROOT / 'build' or not root.is_dir():
        raise ValueError('Generated build root required')
    overlay = ROOT / 'build/pair-runtime-overlay-v1'
    lock = json.loads((overlay / 'pair-runtime-inputs.json').read_text())
    for item in lock['packages']:
        if hashlib.sha256((ROOT / item['file']).read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Runtime package input changed')
    if hashlib.sha256((overlay / 'etc/ssl/certs/ca-certificates.crt').read_bytes()).hexdigest() != lock['ca_sha256']:
        raise ValueError('CA bundle changed')
    # Re-extract verified packages into this private tree, avoiding mutable staging binaries.
    import subprocess
    for item in lock['packages']:
        subprocess.run(['dpkg-deb', '--extract', str(ROOT / item['file']), str(root)], check=True)
    for name in ('common-auth', 'common-account', 'common-password', 'common-session', 'common-session-noninteractive'):
        shutil.copy2(overlay / 'etc/pam.d' / name, root / 'etc/pam.d' / name)
    (root / 'etc/ssl/certs').mkdir(parents=True, exist_ok=True)
    shutil.copy2(overlay / 'etc/ssl/certs/ca-certificates.crt', root / 'etc/ssl/certs/ca-certificates.crt')
    target = seed(root)
    engine = runpy.run_path(str(ROOT / 'scripts/prepare-pair-llamacpp.py'))
    entries = json.loads((ROOT / 'inputs/pair-llamacpp-inputs.json').read_text())['archives']
    inputs = [(entry, engine['verify_input'](entry)) for entry in entries]
    engine['extract'](inputs, target)
    (root / 'usr/lib/gpu-root/pair-runtime.json').write_text(json.dumps({
        'schema': 1, 'llamacpp': 'b11146', 'managed_preinstalled': True,
        'models_dir': '/models', 'engine_autostart': True, 'packages': lock['packages'],
        'contains_cluster_identity': False, 'native_tested': False}, indent=2) + '\n')
