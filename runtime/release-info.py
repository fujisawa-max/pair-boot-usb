#!/usr/bin/env python3
"""Shared release metadata formatting; never read build-host identity."""
import re


def release_number(value):
    if not isinstance(value, str):
        raise ValueError('Unknown root release')
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:-shared)?', value)
    if match is None:
        raise ValueError('Unknown root release')
    return tuple(map(int, match.groups()))


def root_revision(info):
    value = info.get('root_revision')
    # The published 0.9.0 layout predates explicit revisions.
    if value is None and info.get('version') == '0.9.0-shared' and type(info.get('state_schema')) is int and info.get('state_schema') == 1:
        return 0
    if type(value) is not int or value < 1:
        raise ValueError('Unknown root revision')
    return value


def root_identity(info):
    number = release_number(info.get('version'))
    if number < (0, 9, 0):
        raise ValueError('Unsupported legacy root layout')
    if number < (0, 9, 2):
        return (*number, root_revision(info))
    if 'root_revision' in info or info['version'].endswith('-shared'):
        raise ValueError('Unified root release must use its project version')
    if info.get('project_version', info['version']) != info['version']:
        raise ValueError('Project and root versions differ')
    return (*number, 0)


def root_label(info):
    root_identity(info)
    label = info['version'].removesuffix('-shared')
    if 'root_revision' in info:
        label += f" (r{info['root_revision']})"
    return label


def compatible(info, kernel, expected=None):
    try:
        root_identity(info)
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
            'Root release: ' + info['version'],
            'Boot ABI: ' + str(info['boot_abi']), 'State schema: ' + str(info['state_schema'])]
    if 'root_revision' in info:
        rows.insert(3, 'Root revision: ' + str(info['root_revision']))
    if 'registry_schema' in info:
        rows.append('Registry schema: ' + str(info['registry_schema']))
    rows += [name + ': ' + value for name, value in info['components'].items()]
    rows += ['', 'Describes this USB payload. The installed root may differ.',
             'The boot banner identifies the running root and the selected boot mode.', '']
    return '\n'.join(rows)
