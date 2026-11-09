#!/usr/bin/env python3
"""Shared release metadata formatting; never read build-host identity."""
import re


def root_revision(info):
    value = info.get('root_revision')
    # The published 0.9.0 layout predates explicit revisions.
    if value is None and info.get('version') == '0.9.0-shared' and type(info.get('state_schema')) is int and info.get('state_schema') == 1:
        return 0
    if type(value) is not int or value < 1:
        raise ValueError('Unknown root revision')
    return value


def compatible(info, kernel, expected=None):
    try:
        root_revision(info)
    except ValueError:
        return False
    abi = info.get('boot_abi', 1 if info.get('version') == '0.9.0-shared' else None)
    matches = abi == 1 and type(abi) is int and type(info.get('state_schema')) is int and info.get('state_schema') == 1 and info.get('kernel_release') == kernel
    if expected and expected.get('components'):
        components = info.get('components', {})
        if info.get('version') == '0.9.0-shared':
            driver = info.get('pilot_components', {}).get('nvidia_driver_version')
            matches = matches and driver == expected['components']['NVIDIA driver package'].rsplit('-', 1)[0] and info.get('gpu_unlock_commit') == expected['components']['gpu-unlock commit']
        else:
            matches = matches and all(components.get(key) == expected['components'][key] for key in ('NVIDIA driver package', 'gpu-unlock commit'))
    return bool(matches)


def banner_line(info, mode):
    version = info.get('project_version', info.get('version', 'unknown'))
    if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', version):
        version = 'unknown'
    revision = info.get('root_revision')
    suffix = f'    Root rev: {revision}' if type(revision) is int and revision > 0 else ''
    return f'  Version: {version}{suffix}    Mode: {mode}'


def media_text(info):
    rows = ['PAIR BOOT USB', 'Project version: ' + info['project_version'],
            'Root release: ' + info['version'], 'Root revision: ' + str(info['root_revision']),
            'Boot ABI: ' + str(info['boot_abi']), 'State schema: ' + str(info['state_schema'])]
    rows += [name + ': ' + value for name, value in info['components'].items()]
    rows += ['', 'Describes this USB payload. The installed root may differ.',
             'The boot banner identifies the running root and the selected boot mode.', '']
    return '\n'.join(rows)
