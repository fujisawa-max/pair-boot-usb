#!/usr/bin/env python3
"""Release-based root replacement with legacy revisions and downgrade choices."""
import json
import os
import re
import shutil
import uuid
from pathlib import Path
import runpy


def version(value):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:-shared)?', value)
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


def legacy_replace(api, master, target, receipt, manifest, ask=confirm, free_bytes=None, after_rename=None):
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
    return copy_replace(api, master, target, receipt, manifest, free_bytes, after_rename, ask=ask)


def downgrade_choice(old, new, can_keep, can_replace):
    try:
        with open('/dev/console', 'r+') as terminal:
            if not terminal.isatty():
                return 'poweroff'
            terminal.write(f'\nOlder USB root: installed {old}, USB {new}\n'
                           + ('1. DOWNGRADE root and boot (settings retained)\n' if can_replace else '1. Downgrade unavailable: incompatible state/root layout\n')
                           + ('2. Keep installed root and try booting\n' if can_keep else '2. Keep unavailable: incompatible kernel/boot ABI/state\n')
                           + '3. Power off\nChoose 1-3 [Enter = 3]: ')
            terminal.flush()
            answer = terminal.readline().strip()
            if answer == '1' and can_replace:
                terminal.write('Type DOWNGRADE to confirm replacing root; settings will not be rolled back: ')
                terminal.flush()
                return 'replace' if terminal.readline().strip() == 'DOWNGRADE' else 'poweroff'
            if answer == '2' and can_keep:
                return 'keep'
            return 'poweroff'
    except OSError:
        return 'poweroff'


def replace(api, master, target, receipt, manifest, ask=None, free_bytes=None, after_rename=None, choose=downgrade_choice):
    if 'root_revision' not in manifest and version(manifest['version']) < (0, 9, 2):
        return legacy_replace(api, master, target, receipt, manifest, ask=confirm if ask is None else ask,
                              free_bytes=free_bytes, after_rename=after_rename)
    policy = runpy.run_path(str(Path(__file__).with_name('release-info.py')))
    api.regular(target)
    if receipt.get('size') != target.stat().st_size or api.run('losetup', '-j', str(target)):
        raise api.SafetyError('Existing root size changed or image is in use')
    for name in ('.root-replace.json', '.root-replace.img', '.root-replace-receipt.json'):
        if api.safe_child(target.parent, name).exists():
            raise api.SafetyError('Interrupted root replacement; preserve files and inspect from Fedora')
    inside = json.loads(api.run('debugfs', '-R', 'cat /usr/lib/gpu-root/image.json', str(target)))
    if inside.get('version') != receipt.get('version') or inside.get('state_schema') != receipt.get('state_schema'):
        raise api.SafetyError('Existing root release/layout differs from receipt')
    try:
        old = policy['root_identity'](inside)
        new = policy['root_identity'](manifest)
    except ValueError as error:
        raise api.SafetyError(str(error)) from error
    if 'root_revision' in receipt and receipt['root_revision'] != inside.get('root_revision'):
        raise api.SafetyError('Root revision differs from receipt')
    if not re.fullmatch(r'[a-f0-9]{64}', str(receipt.get('original_sha256', ''))):
        raise api.SafetyError('Invalid root receipt hash')
    if 'boot_abi' in receipt and receipt['boot_abi'] != inside.get('boot_abi'):
        raise api.SafetyError('Root boot ABI differs from receipt')
    can_keep = policy['compatible'](inside, manifest['kernel_release'], manifest)
    can_replace = receipt.get('state_schema') == 1 and policy['compatible'](manifest, manifest['kernel_release'], manifest)
    if new == old:
        if receipt.get('original_sha256') != manifest['sha256']:
            kind = 'revision' if 'root_revision' in manifest else 'release'
            raise api.SafetyError(f'Same root {kind} has a different SHA; fix release numbering')
        if not can_keep:
            raise api.SafetyError('Installed root kernel/boot ABI/state incompatible')
        return target
    if new < old:
        if 'root_revision' in inside and 'root_revision' in manifest:
            labels = (inside['root_revision'], manifest['root_revision'])
        else:
            labels = (policy['root_label'](inside), policy['root_label'](manifest))
        action = choose(*labels, can_keep, can_replace)
        if action == 'poweroff':
            raise api.RootPoweroff('User selected poweroff; root and state retained')
        if action == 'keep' and can_keep:
            return target
        if action != 'replace' or not can_replace:
            raise api.SafetyError('Unsupported downgrade choice or incompatible state')
    elif not can_replace:
        raise api.SafetyError('New root is incompatible with persistent state or boot ABI')
    if version(receipt['version']) < (0, 9, 0):
        raise api.SafetyError('Unsupported legacy root layout')
    return copy_replace(api, master, target, receipt, manifest, free_bytes, after_rename)


def copy_replace(api, master, target, receipt, manifest, free_bytes=None, after_rename=None, ask=None):
    parent = target.parent
    api.regular(master)
    progress = api.ImageProgress('Replacing root.img', 5)
    with progress.step('Verifying replacement root.img source', manifest['size']):
        if master.stat().st_size != manifest['size'] or api.digest(master, progress) != manifest['sha256']:
            raise api.SafetyError('Replacement master size/hash mismatch')
        api.validate_ext4(master)
    if 'root_revision' in manifest or 'boot_abi' in manifest:
        inside = json.loads(api.run('debugfs', '-R', 'cat /usr/lib/gpu-root/image.json', str(master)))
        keys = ['version', 'boot_abi', 'state_schema', 'kernel_release']
        if 'root_revision' in manifest:
            keys.append('root_revision')
        for key in keys:
            expected = manifest.get(key, 1 if key == 'state_schema' else None)
            if inside.get(key) != expected:
                raise api.SafetyError('Replacement root metadata differs from manifest: ' + key)
    available = shutil.disk_usage(parent).free if free_bytes is None else free_bytes
    if available < manifest['size'] + max(1024**3, manifest['size'] // 10):
        raise api.SafetyError('Insufficient storage for root replacement and reserve')
    if ask is not None and not ask(receipt['version'], manifest['version']):
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
        with progress.step('Copying replacement root.img', manifest['size']):
            api.copy_image(source, output, progress)
        with progress.step('Flushing replacement root.img to storage'):
            output.flush()
            os.fsync(output.fileno())
    with progress.step('Verifying written replacement root.img', manifest['size']):
        if candidate.stat().st_size != manifest['size'] or api.digest(candidate, progress) != manifest['sha256']:
            raise api.SafetyError('Replacement copy verification failed; original retained')
        api.validate_ext4(candidate)
    with progress.step('Finalizing root.img replacement; retaining previous image'):
        api.new_json(backup_receipt, receipt)
        api.new_json(pending, {'schema': 1, 'state_schema': 1, 'version': manifest['version'],
                               'size': manifest['size'], 'original_sha256': manifest['sha256'],
                               **{key: manifest[key] for key in ('root_revision', 'boot_abi') if key in manifest}})
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
