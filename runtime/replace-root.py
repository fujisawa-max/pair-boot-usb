#!/usr/bin/env python3
"""Confirmed whole-root replacement under the provisioning lock; preserve old files."""
import json
import os
import re
import shutil
import uuid


def version(value):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)-shared', value)
    if not match:
        raise ValueError('Unsupported root release version')
    return tuple(map(int, match.groups()))


def confirm(old, new):
    try:
        with open('/dev/console', 'r+') as terminal:
            if not terminal.isatty():
                return False
            terminal.write(f'\nPAIR BOOT root update: {old} -> {new}\n'
                           'Settings in state.img will be retained. The old root will be backed up.\n'
                           'Replace root? Type UPDATE to continue; Enter cancels: ')
            terminal.flush()
            return terminal.readline().strip() == 'UPDATE'
    except OSError:
        return False


def replace(api, master, target, receipt, manifest, ask=confirm, free_bytes=None, after_rename=None):
    parent = target.parent
    if receipt.get('state_schema') != 1 or manifest.get('persistent_state', {}).get('schema') != 1:
        raise api.SafetyError('Root layout cannot be upgraded; 0.8 and earlier require a fresh installation')
    try:
        old, new = version(receipt['version']), version(manifest['version'])
    except (ValueError, KeyError) as error:
        raise api.SafetyError('Unknown update version') from error
    if old < (0, 9, 0) or new < old:
        raise api.SafetyError('Unsupported legacy update or downgrade')
    api.regular(target)
    if receipt.get('size') != target.stat().st_size or api.run('losetup', '-j', str(target)):
        raise api.SafetyError('Existing root size changed or image is in use')
    for name in ('.root-replace.json', '.root-replace.img', '.root-replace-receipt.json'):
        if api.safe_child(parent, name).exists():
            raise api.SafetyError('Interrupted root replacement; preserve files and inspect from Fedora')
    inside = json.loads(api.run('debugfs', '-R', 'cat /usr/lib/gpu-root/image.json', str(target)))
    if inside.get('version') != receipt['version'] or inside.get('state_schema') != 1:
        raise api.SafetyError('Existing root release/layout differs from receipt')
    api.regular(master)
    if master.stat().st_size != manifest['size'] or api.digest(master) != manifest['sha256']:
        raise api.SafetyError('Replacement master size/hash mismatch')
    api.validate_ext4(master)
    available = shutil.disk_usage(parent).free if free_bytes is None else free_bytes
    if available < manifest['size'] + max(1024**3, manifest['size'] // 10):
        raise api.SafetyError('Insufficient storage for root replacement and reserve')
    if not ask(receipt['version'], manifest['version']):
        raise api.SafetyError('Root update cancelled; incompatible old root was not booted')
    token = uuid.uuid4().hex
    marker = api.safe_child(parent, '.root-replace.json')
    candidate = api.safe_child(parent, '.root-replace.img')
    pending = api.safe_child(parent, '.root-replace-receipt.json')
    backup = api.safe_child(parent, 'root-before-update-' + token + '.img')
    backup_receipt = api.safe_child(parent, 'root-before-update-' + token + '.json')
    api.new_json(marker, {'schema': 1, 'old_receipt': receipt, 'backup': backup.name,
                          'new_sha256': manifest['sha256'], 'new_version': manifest['version']})
    with master.open('rb') as source, candidate.open('xb') as output:
        shutil.copyfileobj(source, output, 8 * 1024**2)
        output.flush()
        os.fsync(output.fileno())
    if candidate.stat().st_size != manifest['size'] or api.digest(candidate) != manifest['sha256']:
        raise api.SafetyError('Replacement copy verification failed; original retained')
    api.validate_ext4(candidate)
    api.new_json(backup_receipt, receipt)
    api.new_json(pending, {'schema': 1, 'state_schema': 1, 'version': manifest['version'],
                           'size': manifest['size'], 'original_sha256': manifest['sha256']})
    api.atomic_new(target, backup)
    api.sync_directory(parent)
    if after_rename:
        after_rename()
    api.atomic_new(candidate, target)
    api.sync_directory(parent)
    os.replace(pending, parent / 'root-image.json')
    api.sync_directory(parent)
    marker.unlink()
    api.sync_directory(parent)
    print('PAIR BOOT: root replaced; previous image retained as ' + backup.name, flush=True)
    return target
