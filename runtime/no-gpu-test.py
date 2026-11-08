#!/usr/bin/env python3
"""Install volatile test presentation and a session mask; never change root.img."""
from pathlib import Path
import shutil


def install(root, state):
    # /run is tmpfs, shared with the future root by dracut switch_root.
    # systemd creates the banner bind only in the console service's namespace.
    shutil.copyfile(root / 'usr/lib/gpu-root/boot-banner.py', state / 'original-banner.py')
    shutil.copyfile('/usr/lib/no-gpu-test.py', state / 'test-banner.py')
    units = Path('/run/systemd/system')
    units.mkdir(parents=True, exist_ok=True)
    (units / 'gpu-pair-session.service').symlink_to('/dev/null')
    drop = units / 'gpu-pair.service.d'
    drop.mkdir()
    (drop / '90-no-gpu-test.conf').write_text(
        '[Service]\nBindReadOnlyPaths=/run/gpu-root/test-banner.py:/usr/lib/gpu-root/boot-banner.py\n')


def render(*args, **kwargs):
    import runpy
    original = runpy.run_path('/run/gpu-root/original-banner.py')
    return original['render'](*args, **kwargs).replace('Mode: unknown', 'Mode: TEST / no GPU')


def display(output):
    import os
    if output.isatty():
        print('\033[0m\033[2J\033[H\033[?25h', end='', file=output)
    print(render(color=output.isatty() and 'NO_COLOR' not in os.environ), file=output)
    print('TEST ONLY: GPU inference is disabled. Start PAIR returns to this menu.\n'
          'Use this boot to check root/state separation and settings persistence.', file=output)
