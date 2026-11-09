#!/usr/bin/env python3
"""Render the running root's release and boot mode on the local console."""
import json
import os
from pathlib import Path
import re
import runpy
import textwrap

GLYPHS = {
    'P': ('#### ', '#   #', '#   #', '#### ', '#    ', '#    ', '#    '),
    'A': (' ### ', '#   #', '#   #', '#####', '#   #', '#   #', '#   #'),
    'I': ('#####', '  #  ', '  #  ', '  #  ', '  #  ', '  #  ', '#####'),
    'R': ('#### ', '#   #', '#   #', '#### ', '# #  ', '#  # ', '#   #'),
    'B': ('#### ', '#   #', '#   #', '#### ', '#   #', '#   #', '#### '),
    'O': (' ### ', '#   #', '#   #', '#   #', '#   #', '#   #', ' ### '),
    'T': ('#####', '  #  ', '  #  ', '  #  ', '  #  ', '  #  ', '  #  '),
}


def render(image_path=Path('/usr/lib/gpu-root/image.json'),
           state_path=Path('/run/gpu-root/state.json'), color=False, columns=100):
    version, mode = 'unknown', 'unknown'
    info = {}
    try:
        info = json.loads(image_path.read_text())
        value = info.get('version')
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9._-]{1,64}', value):
            version = value
    except (OSError, ValueError, AttributeError):
        pass
    try:
        value = json.loads(state_path.read_text()).get('mode')
        if value in ('Normal', 'Unlock', 'TEST'):
            mode = value
    except (OSError, ValueError, AttributeError):
        pass
    # Leave the last column unused to avoid terminal auto-wrap at the edge.
    width = max(1, columns - 1)
    scale = 2 if width >= 98 else 1
    rows = []
    for row in range(7):
        words = ['  '.join(''.join(c * scale for c in GLYPHS[letter][row]) for letter in word)
                 for word in ('PAIR', 'BOOT')]
        if color:
            words = ['\033[96m' + words[0] + '\033[0m', '\033[92m' + words[1] + '\033[0m']
        line = '  ' + '    '.join(words)
        rows.extend((line, line))
    if width < 58:
        rows = textwrap.wrap('PAIR BOOT', width=width)
    helper = runpy.run_path(str(Path(__file__).with_name('release-info.py')))
    rows.extend(textwrap.wrap(helper['banner_line'](info, mode), width=width))
    return '\n'.join(rows)


def terminal_columns(output):
    try:
        columns = os.get_terminal_size(output.fileno()).columns
        return columns if columns > 0 else 80
    except (OSError, ValueError, AttributeError):
        return 80


def display(output):
    tty = output.isatty()
    if tty:
        print('\033[0m\033[2J\033[H\033[?25h', end='', file=output)
    print(render(color=tty and 'NO_COLOR' not in os.environ,
                 columns=terminal_columns(output) if tty else 100), file=output)
