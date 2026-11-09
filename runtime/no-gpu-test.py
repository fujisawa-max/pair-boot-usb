#!/usr/bin/env python3
"""Install volatile test presentation and a session mask; never change root.img."""
from pathlib import Path
import shutil


def install(root, state):
    # /run is tmpfs, shared with the future root by dracut switch_root.
    # systemd creates the banner bind only in the console service's namespace.
    shutil.copyfile(root / 'usr/lib/gpu-root/boot-banner.py', state / 'original-banner.py')
    shutil.copyfile('/usr/lib/no-gpu-test.py', state / 'test-banner.py')
    helper = root / 'usr/lib/gpu-root/release-info.py'
    if helper.is_file():
        shutil.copyfile(helper, state / 'release-info.py')
    units = Path('/run/systemd/system')
    units.mkdir(parents=True, exist_ok=True)
    (units / 'gpu-pair-session.service').symlink_to('/dev/null')
    drop = units / 'gpu-pair.service.d'
    drop.mkdir()
    (drop / '90-no-gpu-test.conf').write_text(
        '[Service]\nBindReadOnlyPaths=/run/gpu-root/test-banner.py:/usr/lib/gpu-root/boot-banner.py\n')


def render(*args, **kwargs):
    import runpy
    import textwrap
    original = runpy.run_path('/run/gpu-root/original-banner.py')
    text = original['render'](*args, **kwargs).replace('Mode: unknown', 'Mode: TEST').replace('Mode: TEST', 'Mode: TEST / no GPU')
    width = max(1, kwargs.get('columns', 100) - 1)
    return '\n'.join(textwrap.fill(line, width=width) if len(line) > width and '\033' not in line
                     else line for line in text.splitlines())


def display(output):
    import os
    import runpy
    import textwrap
    original = runpy.run_path('/run/gpu-root/original-banner.py')
    options = {}
    columns = 100
    if output.isatty() and 'terminal_columns' in original:
        columns = original['terminal_columns'](output)
        options['columns'] = columns
    if output.isatty():
        print('\033[0m\033[2J\033[H\033[?25h', end='', file=output)
    print(render(color=output.isatty() and 'NO_COLOR' not in os.environ, **options), file=output)
    for notice in ('TEST ONLY: GPU inference is disabled. Start PAIR returns to this menu.',
                   'Use this boot to check root/state separation and settings persistence.'):
        print(textwrap.fill(notice, width=max(10, columns - 1)), file=output)
