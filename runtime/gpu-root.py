#!/usr/bin/env python3
"""Fail-closed root image provisioning. Run only from the dedicated initramfs."""
import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import time


class SafetyError(RuntimeError):
    pass


def require_gpu_unlock_loaded(module_root=Path("/sys/module")):
    # The early dracut hook may fail while its systemd unit ignores the exit
    # status. Check again before discovering or mounting any NTFS volume.
    if not (module_root / "gpu_unlock").is_dir():
        raise SafetyError("gpu_unlock was not loaded before storage; check MOK enrollment and kernel log")


def require_no_nvidia(pci_root=Path('/sys/bus/pci/devices')):
    # Read failures are fatal; absence of a driver is not absence of hardware.
    for entry in pci_root.iterdir():
        if (entry / 'vendor').read_text().strip().lower() == '0x10de':
            raise SafetyError('No-GPU test is restricted to PCs without NVIDIA hardware')


def check_boot_mode(config, profile):
    test = config.get('gpu_root.no_gpu_test') == '1'
    if 'gpu_root.no_gpu_test' in config and not test:
        raise SafetyError('Invalid no-GPU test option')
    if test != (profile.get('boot_mode') == 'no-gpu-test'):
        raise SafetyError('Boot menu mode and registered PC mode differ')
    if test and (config.get('gpu_root.shared_registry') != '1' or
                 'storage_target' not in profile or 'gpu_unlock.targets' in config):
        raise SafetyError('No-GPU test requires shared registration, USB storage and no Unlock target')
    return test


def run(*args, **kwargs):
    return subprocess.run(list(args), check=True, text=True, capture_output=True, **kwargs).stdout.strip()


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def regular(path):
    if not stat.S_ISREG(path.lstat().st_mode) or path.is_symlink():
        raise SafetyError(f"Not an ordinary file: {path}")


def safe_child(parent, name, directory=False):
    path = parent / name
    if parent.is_dir():
        aliases = [entry.name for entry in parent.iterdir()
                   if entry.name.casefold() == name.casefold() and entry.name != name]
        if aliases:
            raise SafetyError(f"Windows case collision for {name}: {aliases}")
    if path.is_symlink() or path.resolve().parent != parent.resolve():
        raise SafetyError(f"Unsafe/reparse path: {path}")
    if directory and path.exists() and not path.is_dir():
        raise SafetyError(f"Not a directory: {path}")
    return path


def rename_noreplace(source, destination):
    """Linux renameat2 NOREPLACE when supported by the filesystem."""
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def atomic_new(source, destination):
    """Publish under the caller's flock on the private root-only backing mount.

    ntfs-3g's FUSE protocol lacks RENAME2. Reserve an empty, transaction-owned
    target with O_EXCL before ordinary atomic rename; an existing image is
    never replaced. Only the trusted boot controller may access this directory.
    """
    try:
        rename_noreplace(source, destination)
        return
    except OSError as error:
        if error.errno not in (errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP):
            raise
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        reserved = os.fstat(fd)
        current = destination.lstat()
        if (reserved.st_dev, reserved.st_ino) != (current.st_dev, current.st_ino) or current.st_size != 0:
            raise SafetyError("Reserved destination changed; refusing rename")
        os.rename(source, destination)
    finally:
        os.close(fd)


def new_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def manifest_at(path):
    regular(path)
    value = json.loads(path.read_text())
    if value.get("schema") != 1 or not re.fullmatch(r"[a-zA-Z0-9._-]{1,64}", value.get("version", "")):
        raise SafetyError("Invalid master manifest schema/version")
    if not re.fullmatch(r"[a-f0-9]{64}", value.get("sha256", "")):
        raise SafetyError("Invalid master hash")
    if not isinstance(value.get("size"), int) or value["size"] < 16 * 1024 * 1024:
        raise SafetyError("Invalid master size")
    return value


def validate_ext4(path):
    regular(path)
    if run("blkid", "-p", "-s", "TYPE", "-o", "value", str(path)) != "ext4":
        raise SafetyError("Root image is not ext4")
    with path.open("rb") as stream:
        stream.seek(1024)
        superblock = stream.read(1024)
    if len(superblock) != 1024:
        raise SafetyError("Truncated ext4 superblock")
    state = struct.unpack_from("<H", superblock, 0x3a)[0]
    incompat = struct.unpack_from("<I", superblock, 0x60)[0]
    ro_compat = struct.unpack_from("<I", superblock, 0x64)[0]
    if not state & 1 or state & 2 or incompat & 4 or ro_compat & 0x10000:
        raise SafetyError("ext4 is unclean or needs journal replay; manual offline review required")
    # Never repair even the writable copy automatically. Every nonzero status stops boot.
    run("e2fsck", "-f", "-n", str(path))


def provision(master, parent, manifest, free_bytes=None, copy_hook=None, maintenance=None, prepare_new=None):
    """Parent is on an already checked dedicated NTFS mount; fixtures use tmpdirs."""
    if not parent.is_dir() or parent.is_symlink():
        raise SafetyError("Unsafe GPU-Linux directory")
    marker = safe_child(parent, ".root-copy-in-progress")
    temporary = safe_child(parent, ".root.img.partial")
    receipt = safe_child(parent, "root-image.json")
    target = safe_child(parent, "root.img")
    # Lock is private to Linux's root-only NTFS mount. Windows must be fully off.
    lock = safe_child(parent, ".root-image.lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if marker.exists() or temporary.exists():
            raise SafetyError("Incomplete copy transaction; manual review required; no auto cleanup")
        for name in ('.root-maintenance.json', '.root-maintenance.img', '.root-maintenance-receipt.json',
                     '.root-replace.json', '.root-replace.img', '.root-replace-receipt.json',
                     '.state-copy-in-progress', '.state.img.partial', '.state-maintenance.json',
                     '.state-maintenance.img', '.state-maintenance-receipt.json'):
            if safe_child(parent, name).exists():
                raise SafetyError('Interrupted root maintenance; preserve files and review from Fedora')
        if target.exists():
            regular(target)
            regular(receipt)
            info = json.loads(receipt.read_text())
            if maintenance is not None:
                if info.get('schema') != 1 or info.get('size') != target.stat().st_size:
                    raise SafetyError('Existing root receipt size/schema mismatch')
                return maintenance(target, info), False
            if info.get("version") != manifest["version"] or info.get("size") != target.stat().st_size:
                raise SafetyError("Existing image version/size mismatch; never overwritten")
            if info.get("original_sha256") != manifest["sha256"]:
                raise SafetyError("Master release hash changed; explicit migration required")
            # Root is mutable: bind receipt to the master, not the root's current content hash.
            return target, False
        if receipt.exists():
            raise SafetyError("Receipt exists without image; manual review required")
        regular(master)
        if master.stat().st_size != manifest["size"] or digest(master) != manifest["sha256"]:
            raise SafetyError("Master size/SHA-256 mismatch")
        free = shutil.disk_usage(parent).free if free_bytes is None else free_bytes
        # Require full logical size even for sparse files, plus a 1GiB/10% reserve.
        reserve = max(1024**3, manifest["size"] // 10)
        if free < manifest["size"] + reserve:
            raise SafetyError(f"Insufficient NTFS space: need {manifest['size'] + reserve}, available {free}")
        new_json(marker, {"version": manifest["version"], "state": "copying"})
        out_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with master.open("rb") as source, os.fdopen(out_fd, "wb") as output:
            while block := source.read(8 * 1024 * 1024):
                output.write(block)
                if copy_hook:
                    copy_hook(output)
            output.flush()
            os.fsync(output.fileno())
        if temporary.stat().st_size != manifest["size"] or digest(temporary) != manifest["sha256"]:
            raise SafetyError("Copy size/SHA-256 mismatch; transaction retained")
        if prepare_new is not None:
            prepare_new()
        atomic_new(temporary, target)
        sync_directory(parent)
        new_json(receipt, {"schema": 1, "version": manifest["version"], "size": manifest["size"],
                           "original_sha256": manifest["sha256"],
                           **({"state_schema": 1} if manifest.get("persistent_state") else {})})
        marker.unlink()
        sync_directory(parent)
        return target, True
    finally:
        os.close(fd)


def check_volume(kind, probe_code):
    if kind.lower() in ("bitlocker", "bitlocker2"):
        raise SafetyError("BitLocker volume: no unlock, key retrieval, or changes attempted")
    if kind != "ntfs":
        raise SafetyError(f"Expected plain NTFS, got {kind!r}")
    if probe_code != 0:
        reason = {13: "inconsistent NTFS", 14: "Windows hibernated/Fast Startup", 15: "unclean NTFS",
                  16: "volume already in use"}.get(probe_code, "NTFS probe failed")
        raise SafetyError(f"{reason} (probe={probe_code}); refusing all NTFS writes")


def device(uuid):
    if not re.fullmatch(r"[A-Za-z0-9-]{4,64}", uuid):
        raise SafetyError("An explicit filesystem UUID is required")
    output = run("blkid", "-c", "/dev/null", "-t", f"UUID={uuid}", "-o", "device").splitlines()
    if len(output) != 1 or not stat.S_ISBLK(os.stat(output[0]).st_mode):
        raise SafetyError("UUID must resolve to exactly one block device")
    return output[0]


def cmdline(text):
    values = {}
    for token in text.split():
        if (token.startswith("gpu_root.") or token.startswith('gpu_unlock.targets=')) and "=" in token:
            key, value = token.split("=", 1)
            if key in values:
                raise SafetyError(f"Duplicate boot option: {key}")
            values[key] = value
    return values


def host_profile(usb_mount, uuid, now=None):
    hardware = uuid.strip().lower()
    if not re.fullmatch(r"[a-f0-9-]{36}", hardware) or hardware in ("00000000-0000-0000-0000-000000000000", "ffffffff-ffff-ffff-ffff-ffffffffffff"):
        raise SafetyError("Missing/ambiguous DMI product UUID; provision a reviewed host mapping")
    path = usb_mount / "gpu-linux/hosts" / (hardware + ".json")
    regular(path)
    profile = json.loads(path.read_text())
    if profile.get("schema") != 1 or profile.get("hardware_uuid", "").lower() != hardware:
        raise SafetyError("Host inventory does not match this PC")
    if profile.get("bitlocker_volume_status") != "FullyDecrypted" or profile.get("bitlocker_protection") != "Off":
        raise SafetyError("BitLocker is enabled or state is unknown; stop without changes")
    if profile.get("fast_startup_enabled") is not False:
        raise SafetyError("Windows Fast Startup is enabled or unknown; stop without changes")
    age = (time.time() if now is None else now) - profile.get("checked_at_unix", 0)
    if age < 0 or age > 30 * 86400:
        raise SafetyError("Windows safety inventory expired/future dated; refresh from Windows")
    return profile


def configuration_device(usb):
    name = Path(os.path.realpath(usb)).name
    partition = (Path('/sys/class/block') / name).resolve()
    if (partition / 'partition').read_text().strip() != '2':
        raise SafetyError('Shared registry requires master partition 2')
    parent = partition.parent
    if run('lsblk', '-dn', '-o', 'TRAN', '/dev/' + parent.name) != 'usb':
        raise SafetyError('Master parent must be a physical USB disk')
    candidates = [entry for entry in parent.iterdir()
                  if (entry / 'partition').is_file() and (entry / 'partition').read_text().strip() == '3']
    if len(candidates) != 1:
        raise SafetyError('Expected exactly one configuration partition 3 on the same USB')
    selected = '/dev/' + candidates[0].name
    if run('blkid', '-p', '-s', 'TYPE', '-o', 'value', selected) != 'vfat' or run('blkid', '-p', '-s', 'LABEL', '-o', 'value', selected) != 'GPU-LOGS':
        raise SafetyError('Expected same-USB GPU-LOGS FAT partition')
    return selected


def shared_host_profile(usb, manifest, hardware, state):
    import importlib.util
    spec = importlib.util.spec_from_file_location('fleet_registry', '/usr/lib/fleet-registry.py')
    fleet = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fleet)
    mountpoint = state / 'config'
    mountpoint.mkdir(exist_ok=True)
    selected = configuration_device(usb)
    run('mount', '-t', 'vfat', '-o', 'ro,nodev,nosuid,noexec', selected, str(mountpoint))
    try:
        marker_path = mountpoint / 'usb-config.json'
        regular(marker_path)
        marker = json.loads(marker_path.read_text())
        if marker != {'schema': 1, 'kind': 'gpu-linux-config', 'root_version': manifest['version']}:
            raise SafetyError('Configuration partition root version mismatch')
        value = fleet.load(mountpoint / 'gpu-config/registry.json')
        value = fleet.for_version(value, manifest['version'])
        hardware = fleet.hardware_uuid(hardware.strip())
        matches = [item for item in value['records'] if item['hardware_uuid'] == hardware]
        if len(matches) != 1:
            raise SafetyError('This PC is not registered; use Fedora fleet-register first')
        return matches[0]
    finally:
        run('umount', str(mountpoint))


def mount_readwrite(device_path, mountpoint):
    # Keep FUSE root's daemon out of systemd's blanket process kill (@argv[0]).
    log = open("/run/gpu-root/ntfs.log", "a")
    proc = subprocess.Popen(["@gpu-root-ntfs", "-o",
                             "rw,norecover,no_def_opts,no_detach,allow_other,default_permissions,nodev,nosuid,noexec,windows_names,uid=0,gid=1000,dmask=027,fmask=137",
                             device_path, str(mountpoint)], executable="/usr/bin/ntfs-3g",
                            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, cwd="/run/gpu-root")
    log.close()
    for _ in range(100):
        if proc.poll() is not None:
            raise SafetyError("ntfs-3g exited; see /run/gpu-root/ntfs.log")
        if os.path.ismount(mountpoint):
            options = run("findmnt", "-n", "-o", "OPTIONS", "--target", str(mountpoint)).split(",")
            if "rw" not in options:
                raise SafetyError("NTFS silently fell back to read-only; refusing boot")
            return
        time.sleep(0.1)
    raise SafetyError("NTFS mount timeout")


def validate_storage_directory(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9._-]{0,63}', value) or value.endswith('.'):
        raise ValueError('Storage directory must be one safe ASCII folder name (1-64 characters)')
    stem = value.split('.', 1)[0].lower()
    if stem in {'con', 'prn', 'aux', 'nul', 'windows', 'users', 'programdata', 'boot', 'recovery', 'efi', 'perflogs', 'msocache'} or re.fullmatch(r'(com|lpt)[1-9]', stem):
        raise ValueError('Reserved Windows/device directory name')
    return value


def storage_paths(ntfs_mount, directory="GPU-Linux"):
    try:
        validate_storage_directory(directory)
    except ValueError as error:
        raise SafetyError(str(error)) from error
    parent = safe_child(ntfs_mount, directory, directory=True)
    if not parent.exists():
        parent.mkdir(mode=0o700)
        sync_directory(ntfs_mount)
    models = safe_child(parent, "Models", directory=True)
    if not models.exists():
        models.mkdir(mode=0o750)
        sync_directory(parent)
    return parent, models


def storage_policy():
    import importlib.util
    policy_path = Path(__file__).with_name('storage-target.py')
    spec = importlib.util.spec_from_file_location('root_storage_target', policy_path)
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    return policy


def verify_root_storage(profile, selected, boot_usb):
    """USB roots require an explicit reviewed GPT binding, rechecked each boot."""
    target = profile.get('storage_target')
    parent = run('lsblk', '-dn', '-o', 'PKNAME', selected)
    if not re.fullmatch(r'[A-Za-z0-9._+-]+', parent):
        raise SafetyError('Cannot identify root storage physical parent')
    transport = run('lsblk', '-dn', '-o', 'TRAN', '/dev/' + parent).lower()
    same_usb = False
    if target is not None:
        policy = storage_policy()
        if transport != 'usb' or policy.inspect_usb_target(selected, run) != target:
            raise SafetyError('USB transport/GPT identity changed since registration')
        boot_parent = run('lsblk', '-dn', '-o', 'PKNAME', boot_usb)
        if not re.fullmatch(r'[A-Za-z0-9._+-]+', boot_parent):
            raise SafetyError('Cannot identify boot USB parent')
        same_usb = parent == boot_parent
        if same_usb:
            # The integrated image reserves partitions 1-4 for boot/master/config/Live.
            # A same-device root may only use an explicitly added data partition.
            number = (Path('/sys/class/block') / Path(selected).name / 'partition').read_text().strip()
            if not number.isdecimal() or int(number) < 5:
                raise SafetyError('Root target overlaps a reserved boot USB partition')
    elif transport not in ('nvme', 'sata'):
        raise SafetyError('USB root target requires explicit Fedora-reviewed USB registration')
    return {'root_storage_device': selected, 'root_storage_transport': transport,
            'root_storage_on_boot_usb': same_usb}


def boot(config):
    if os.getpid() == 1:
        raise SafetyError("Controller must be invoked by dracut, not as PID 1")
    if not Path("/etc/initrd-release").exists() or config.get("gpu_root.allow_ntfs_write") != "1":
        raise SafetyError("Only dedicated initramfs with explicit gpu_root.allow_ntfs_write=1 may run")
    test = config.get('gpu_root.no_gpu_test') == '1'
    if test:
        require_no_nvidia()
    else:
        require_gpu_unlock_loaded()
    usb = device(config.get("gpu_root.usb_uuid", ""))
    state = Path("/run/gpu-root")
    state.mkdir(mode=0o700, exist_ok=True)
    usb_mount = state / "usb"
    ntfs_mount = state / "ntfs"
    for path in (usb_mount, ntfs_mount):
        path.mkdir(exist_ok=True)
    loop = None
    root_mounted = False
    models_mounted = False
    persistent_mounts = []
    state_loop = None
    try:
        # ext4 USB data partition: noload prevents even journal replay to the master volume.
        if run("blkid", "-p", "-s", "TYPE", "-o", "value", usb) != "ext4":
            raise SafetyError("USB master data partition must be ext4 (separate from EFI FAT)")
        run("mount", "-t", "ext4", "-o", "ro,noload,nodev,nosuid,noexec", usb, str(usb_mount))
        manifest = manifest_at(usb_mount / "gpu-linux/master.json")
        if manifest["kernel_release"] != os.uname().release:
            raise SafetyError("USB kernel and master image version do not match")
        hardware = Path("/sys/class/dmi/id/product_uuid").read_text()
        if config.get('gpu_root.shared_registry') == '1':
            profile = shared_host_profile(usb, manifest, hardware, state)
        else:
            profile = host_profile(usb_mount, hardware)
        check_boot_mode(config, profile)
        configured_uuid = config.get("gpu_root.ntfs_uuid", profile["ntfs_uuid"])
        if configured_uuid.upper() != profile["ntfs_uuid"].upper():
            raise SafetyError("Explicit NTFS UUID conflicts with this PC inventory")
        ntfs = device(configured_uuid)
        if usb == ntfs:
            raise SafetyError("USB and Windows volume cannot be the same device")
        kind = run("blkid", "-p", "-s", "TYPE", "-o", "value", ntfs)
        check_volume(kind, 0)
        if subprocess.run(["findmnt", "-n", "-S", ntfs], stdout=subprocess.DEVNULL).returncode == 0:
            raise SafetyError("NTFS is already mounted; refusing reuse")
        selected = [word.split("=", 1)[1] for word in Path("/proc/cmdline").read_text().split()
                    if word.startswith("gpu_unlock.targets=")]
        if manifest.get('automatic_root_recovery') is True and not test:
            import runpy
            policy = runpy.run_path('/usr/lib/fleet-registry.py')
            gpu = Path('/sys/bus/pci/devices') / profile['gpu_bdf']
            actual = (gpu / 'vendor').read_text().strip().removeprefix('0x') + ':' + (gpu / 'device').read_text().strip().removeprefix('0x')
            if actual.lower() != profile['gpu_pci_id']:
                raise SafetyError('Registered GPU PCI identity changed; review from Fedora')
            if selected and profile['gpu_pci_id'] not in policy['GPU_UNLOCK_IDS']:
                raise SafetyError('GPU supports Normal mode only in this release; reboot and select Normal')
        if selected and (len(selected) != 1 or selected[0].lower() != profile.get("gpu_bdf", "").lower()):
            raise SafetyError("Unlock target must match the explicit GPU BDF in this host inventory")
        master = usb_mount / "gpu-linux/root.img"
        regular(master)
        if digest(master) != manifest["sha256"]:
            raise SafetyError("Master SHA-256 mismatch before NTFS mount")
        validate_ext4(master)
        import runpy
        import types
        state_api = types.SimpleNamespace(**globals())
        template = runpy.run_path('/usr/lib/persistent-state.py')['metadata'](state_api, manifest)
        state_master = usb_mount / 'gpu-linux/state.img'
        regular(state_master)
        if state_master.stat().st_size != template['size'] or digest(state_master) != template['sha256']:
            raise SafetyError('State master hash/size mismatch before NTFS mount')
        validate_ext4(state_master)
        storage_state = verify_root_storage(profile, ntfs, usb)
        # The readwrite probe opens NTFS with write capability. Run it only
        # after the inventory, device, mount-state, target and master guards.
        probe = subprocess.run(["ntfs-3g.probe", "--readwrite", ntfs], capture_output=True).returncode
        check_volume(kind, probe)
        mount_readwrite(ntfs, ntfs_mount)
        parent, models = storage_paths(ntfs_mount, profile.get("storage_directory", "GPU-Linux"))
        import runpy
        import types
        api = types.SimpleNamespace(**globals())
        helper = runpy.run_path('/usr/lib/root-maintenance.py')['maintain']
        persistent = runpy.run_path('/usr/lib/persistent-state.py')
        state_meta = persistent['metadata'](api, manifest)
        state_master = usb_mount / 'gpu-linux/state.img'
        def create_state():
            persistent['create'](api, state_master, parent, state_meta, hardware)
        def maintenance(target, receipt):
            if receipt.get('state_schema') != 1:
                raise SafetyError('0.8 and earlier require a fresh installation in a new storage folder')
            state_image, state_receipt = persistent['inspect'](api, parent, hardware)
            if receipt.get('version') != manifest['version'] or receipt.get('original_sha256') != manifest['sha256']:
                replacement = runpy.run_path('/usr/lib/replace-root.py')['replace']
                return replacement(api, master, target, receipt, manifest)
            return helper(api, target, receipt, manifest, None, state)
        image, created = provision(master, parent, manifest, maintenance=maintenance, prepare_new=create_state)
        state_image, state_receipt = persistent['inspect'](api, parent, hardware)
        state_release = {'version': 'state-v1', 'sha256': state_receipt['original_sha256']}
        helper(api, state_image, state_receipt, state_release, None, state, prefix='state')
        validate_ext4(state_image)
        validate_ext4(image)
        # Inspect release inside the filesystem without mounting it writable.
        inside = run("debugfs", "-R", "cat /usr/lib/gpu-root/image.json", str(image))
        release = json.loads(inside)
        if release.get("version") != manifest["version"] or release.get("kernel_release") != os.uname().release:
            raise SafetyError("Existing root version/kernel incompatible; no automatic update")
        loop = run("losetup", "--find", "--show", "--nooverlap", str(image))
        run("mount", "-t", "ext4", "-o", "rw,noatime", loop, "/sysroot")
        root_mounted = True
        if test:
            runpy.run_path('/usr/lib/no-gpu-test.py')['install'](Path('/sysroot'), state)
        state_loop = persistent['attach'](api, state_image, hardware, Path('/sysroot'), persistent_mounts)
        run("mount", "--bind", str(models), "/sysroot/models")
        models_mounted = True
        run("mount", "-o", "remount,bind,ro,nodev,nosuid,noexec", "/sysroot/models")
        run("umount", str(usb_mount))
        new_json(state / "state.json", {"loop": loop, "created": created,
                                        "version": manifest["version"], "state_schema": 1, "state_loop": state_loop,
                                        "mode": "TEST" if test else ("Unlock" if selected else "Normal"),
                                        "usb_device": usb, "usb_unmounted": True, **storage_state})
        # /run survives switch_root; shutdown hook tears down root -> loop -> NTFS.
    except BaseException:
        if models_mounted:
            run("umount", "/sysroot/models")
        for path in reversed(persistent_mounts):
            run("umount", str(path))
        if state_loop:
            run("losetup", "--detach", state_loop)
        if root_mounted:
            run("umount", "/sysroot")
        if loop:
            run("losetup", "--detach", loop)
        if os.path.ismount(ntfs_mount):
            run("sync", "-f", str(ntfs_mount))
            run("umount", str(ntfs_mount))
        if os.path.ismount(usb_mount):
            run("umount", str(usb_mount))
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["boot"])
    parser.parse_args()
    try:
        boot(cmdline(Path("/proc/cmdline").read_text()))
    except (OSError, ValueError, KeyError, TypeError, SafetyError, subprocess.CalledProcessError) as error:
        sys.exit(f"GPU-ROOT STOP: {error}")
