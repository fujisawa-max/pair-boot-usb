#!/usr/bin/env python3
"""Copy-only repair and bounded runtime migration, called under the root lock."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import uuid


def apply_upgrade(api, candidate, payload, metadata, workspace):
    """Mount only the private candidate; install a hash-checked file allowlist."""
    if api.digest(payload) != metadata['sha256']:
        raise api.SafetyError('Runtime upgrade payload hash mismatch')
    allowed = {'usr/lib/gpu-root/image.json', 'usr/lib/gpu-root/pair-console.py',
               'usr/lib/gpu-root/announce-usb-ready.py', 'usr/lib/gpu-root/prepare-shutdown.py',
               'usr/local/bin/gpu-pair', 'usr/lib/systemd/system/gpu-pair.service',
               'usr/lib/systemd/system/gpu-root-console.service',
               'boot/initramfs-6.12.113+deb13-amd64.img'}
    moddir = 'usr/lib/dracut/modules.d/95gpu-root/'
    files = metadata['files']
    if not isinstance(files, list) or not files or len(files) > 80:
        raise api.SafetyError('Invalid runtime upgrade file list')
    names = [item['path'] for item in files]
    if len(names) != len(set(names)) or not allowed <= set(names):
        raise api.SafetyError('Incomplete/duplicate runtime upgrade file list')
    for name in names:
        if name not in allowed and not (name.startswith(moddir) and
                Path(name).name == name[len(moddir):] and '..' not in Path(name).parts):
            raise api.SafetyError('Upgrade path is outside runtime allowlist')
    mount = workspace / 'upgrade-root'
    mount.mkdir(exist_ok=True)
    loop = api.run('losetup', '--find', '--show', '--nooverlap', str(candidate))
    mounted = False
    try:
        api.run('mount', '-t', 'ext4', '-o', 'rw,nodev,nosuid,noexec', loop, str(mount))
        mounted = True
        with tarfile.open(payload, 'r:') as archive:
            members = archive.getmembers()
            if len(members) != len(names) or {m.name for m in members} != set(names):
                raise api.SafetyError('Upgrade archive differs from allowlist')
            for item in files:
                member = archive.getmember(item['path'])
                if not member.isfile() or member.size != item['size'] or item['mode'] not in (0o644, 0o755):
                    raise api.SafetyError('Upgrade contains nonregular or unexpected file')
                target = mount / item['path']
                for parent in target.relative_to(mount).parents:
                    if (mount / parent).is_symlink():
                        raise api.SafetyError('Upgrade destination traverses a symlink')
                if target.is_symlink() or (target.exists() and not target.is_file()):
                    raise api.SafetyError('Upgrade destination is not a regular file')
                target.parent.mkdir(parents=True, exist_ok=True)
                data = archive.extractfile(member)
                # All members are bounded by their authenticated build metadata.
                temporary = target.with_name('.' + target.name + '.v0.8-new')
                with temporary.open('xb') as out:
                    shutil.copyfileobj(data, out, 8 * 1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
                if api.digest(temporary) != item['sha256']:
                    raise api.SafetyError('Upgrade member hash mismatch')
                temporary.chmod(item['mode'])
                os.replace(temporary, target)
        api.run('sync', '-f', str(mount))
    finally:
        # Never detach a loop if unmount failed. Boot stays stopped for review.
        if mounted:
            api.run('umount', str(mount))
        api.run('losetup', '-d', loop)


def maintain(api, target, receipt, manifest, payload, workspace, free_bytes=None, upgrade=None, prefix="root"):
    """The caller already verified the selected NTFS and holds .root-image.lock."""
    if prefix not in ('root', 'state'):
        raise api.SafetyError('Unknown maintenance image kind')
    parent = target.parent
    marker = api.safe_child(parent, '.' + prefix + '-maintenance.json')
    candidate = api.safe_child(parent, '.' + prefix + '-maintenance.img')
    pending_receipt = api.safe_child(parent, '.' + prefix + '-maintenance-receipt.json')
    if any(p.exists() for p in (marker, candidate, pending_receipt)):
        raise api.SafetyError('Interrupted root maintenance; preserve files and review from Fedora')
    api.regular(target)
    if api.run('losetup', '-j', str(target)):
        raise api.SafetyError('Root image is already attached; automatic recovery refused')
    if api.run('blkid', '-p', '-s', 'TYPE', '-o', 'value', str(target)) != 'ext4':
        raise api.SafetyError('Only an identified ext4 root image may be recovered')
    migration = receipt['version'] != manifest['version']
    if migration:
        previous = manifest.get('migration', {})
        if (receipt['version'], receipt.get('original_sha256'), receipt['size']) != (
                previous.get('from_version'), previous.get('from_sha256'), previous.get('from_size')):
            raise api.SafetyError('Existing root has no reviewed migration path')
    else:
        if receipt.get('original_sha256') != manifest['sha256']:
            raise api.SafetyError('Master release hash changed; explicit migration required')
        try:
            api.validate_ext4(target)
            return target
        except (api.SafetyError, subprocess.CalledProcessError):
            pass
    size = target.stat().st_size
    available = shutil.disk_usage(parent).free if free_bytes is None else free_bytes
    if size != receipt['size'] or available < size + max(1024**3, size // 10):
        raise api.SafetyError('Root size mismatch or insufficient space for preserved-copy recovery')
    token = uuid.uuid4().hex
    backup = api.safe_child(parent, prefix + '-backup-' + token + '.img')
    backup_receipt = api.safe_child(parent, prefix + '-backup-' + token + '.json')
    log_path = api.safe_child(parent, prefix + '-maintenance-' + token + '.log')
    api.new_json(marker, {'schema': 1, 'state': 'copy-repair-upgrade', 'backup': backup.name,
                          'old_receipt': receipt, 'new_version': manifest['version'], 'log': log_path.name})
    print('GPU-ROOT: preparing preserved-copy recovery/upgrade; do not remove root storage', flush=True)
    with target.open('rb') as source, candidate.open('xb') as output:
        expected = hashlib.sha256()
        while block := source.read(8 * 1024 * 1024):
            expected.update(block)
            output.write(block)
        output.flush()
        os.fsync(output.fileno())
    if candidate.stat().st_size != size or api.digest(candidate) != expected.hexdigest():
        raise api.SafetyError('Recovery copy verification failed; original preserved')
    with log_path.open('xb') as log:
        try:
            result = subprocess.run(['e2fsck', '-p', '-f', str(candidate)], stdout=log,
                                    stderr=subprocess.STDOUT, timeout=600)
        except subprocess.TimeoutExpired as error:
            raise api.SafetyError('Automatic fsck timed out; original preserved; review from Fedora') from error
        finally:
            log.flush()
            os.fsync(log.fileno())
    if result.returncode not in (0, 1):
        raise api.SafetyError(f'Automatic fsck stopped (exit={result.returncode}); original preserved; see {log_path.name}')
    api.validate_ext4(candidate)
    if migration:
        release = json.loads(api.run('debugfs', '-R', 'cat /usr/lib/gpu-root/image.json', str(candidate)))
        if release.get('version') != receipt['version'] or release.get('kernel_release') != manifest['kernel_release']:
            raise api.SafetyError('Legacy root release/kernel mismatch; original preserved')
        if upgrade is None:
            apply_upgrade(api, candidate, payload, manifest['migration'], workspace)
        else:
            upgrade(candidate)
        api.validate_ext4(candidate)
        release = json.loads(api.run('debugfs', '-R', 'cat /usr/lib/gpu-root/image.json', str(candidate)))
        if release.get('version') != manifest['version'] or release.get('kernel_release') != manifest['kernel_release']:
            raise api.SafetyError('Upgraded root version/kernel mismatch; original preserved')
    api.new_json(backup_receipt, receipt)
    api.new_json(pending_receipt, {**receipt, 'schema': 1, 'version': manifest['version'], 'size': size,
                                 'original_sha256': manifest['sha256']})
    api.sync_directory(parent)
    api.atomic_new(target, backup)
    api.sync_directory(parent)
    api.atomic_new(candidate, target)
    api.sync_directory(parent)
    os.replace(pending_receipt, parent / (prefix + '-image.json'))
    api.sync_directory(parent)
    marker.unlink()
    api.sync_directory(parent)
    print('GPU-ROOT: recovery/upgrade verified; previous root retained as ' + backup.name, flush=True)
    return target
