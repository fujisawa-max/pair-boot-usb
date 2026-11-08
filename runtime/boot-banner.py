#!/usr/bin/env python3
"""Render the running root's release and boot mode on the local console."""
import json
import os
from pathlib import Path
import re

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
           state_path=Path('/run/gpu-root/state.json'), color=False):
    version, mode = 'unknown', 'unknown'
    try:
        value = json.loads(image_path.read_text()).get('version')
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9._-]{1,64}', value):
            version = value
    except (OSError, ValueError, AttributeError):
        pass
    try:
        value = json.loads(state_path.read_text()).get('mode')
        if value in ('Normal', 'Unlock'):
            mode = value
    except (OSError, ValueError, AttributeError):
        pass
    rows = []
    for row in range(7):
        words = ['  '.join(''.join(c * 2 for c in GLYPHS[letter][row]) for letter in word)
                 for word in ('PAIR', 'BOOT')]
        if color:
            words = ['\033[96m' + words[0] + '\033[0m', '\033[92m' + words[1] + '\033[0m']
        line = '  ' + '    '.join(words)
        rows.extend((line, line))
    rows.append(f'  Version: {version}    Mode: {mode}')
    return '\n'.join(rows)


def display(output):
    tty = output.isatty()
    if tty:
        print('\033[0m\033[2J\033[H\033[?25h', end='', file=output)
    print(render(color=tty and 'NO_COLOR' not in os.environ), file=output)
