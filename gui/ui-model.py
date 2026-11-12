"""Shared operation definitions and UI messages. Importing never accesses devices."""
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
MESSAGES = json.loads((HERE / 'ui-text.json').read_text())
OPERATIONS = json.loads((HERE / 'operations.json').read_text())


def language(environ=None):
    env = os.environ if environ is None else environ
    locale = env.get('LC_ALL') or env.get('LC_MESSAGES') or env.get('LANG') or 'C'
    if locale.split('.')[0] in ('C', 'POSIX'):
        return 'en'
    preferred = (env.get('LANGUAGE') or locale).split(':')[0]
    return 'ja' if preferred.lower().replace('-', '_').split('_')[0] == 'ja' else 'en'


def text(key, lang=None):
    value = MESSAGES[key]
    return value.get(lang or language(), value['en'])


def operations(platform, category=None):
    return [dict(item) for item in OPERATIONS[platform]
            if category is None or item['category'] == category]


def pc_rows(registry, lang=None):
    return [(item.get('windows_computer_name') or text('unnamed', lang),
             text('test' if item.get('boot_mode') == 'no-gpu-test' else 'normal', lang),
             item['storage_directory'], item['hardware_uuid']) for item in registry['records']]
