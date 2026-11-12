#!/usr/bin/env python3
"""Inspect generated file artifacts without mounting images or loading modules."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import struct
import subprocess
import sys
import tempfile


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_mokmanager(tree):
    if (tree / "EFI/BOOT/signing-cert.der").is_file():
        manager = tree / "EFI/BOOT/mmx64.efi"
        if not manager.is_file() or manager.read_bytes()[:2] != b"MZ":
            raise ValueError("Signed EFI tree lacks MokManager EFI/BOOT/mmx64.efi")


def check_host_gpu_addresses(tree):
    paths = list((tree / 'gpu-linux/hosts').glob('*.json'))
    if not paths:
        manifest = json.loads((tree / 'gpu-linux/master.json').read_text())
        if manifest.get('shared_registry') is True:
            return
        raise ValueError('Missing reviewed host inventory')
    for path in paths:
        profile = json.loads(path.read_text())
        if not re.fullmatch(r'[a-f0-9]{4}:[a-f0-9]{2}:[a-f0-9]{2}\.[0-7]', profile.get('gpu_bdf', '')):
            raise ValueError('Host inventory lacks explicit GPU PCI address: ' + path.name)


def check_shutdown_restore(tree):
    sysroot = tree.resolve().parent / 'root-tree'
    helper = sysroot / 'usr/lib/gpu-root/prepare-shutdown.py'
    if not helper.exists():
        return
    if helper.read_bytes() != (Path(__file__).resolve().parents[1] / 'runtime/prepare-shutdown.py').read_bytes():
        raise ValueError('Obsolete shutdown preparation helper')
    code = ('import runpy; from pathlib import Path; '
            'm=runpy.run_path("/usr/lib/gpu-root/prepare-shutdown.py"); '
            'm["prepare"](next(Path("/boot").glob("initramfs-*.img")),Path("/run/initramfs")); '
            'assert not Path("/run/initramfs/.need_shutdown").exists(); '
            'print("Actual shutdown RAM restore verified")')
    result = subprocess.run(['bwrap', '--unshare-all', '--cap-add', 'CAP_SYS_CHROOT',
                             '--ro-bind', str(sysroot), '/', '--dev', '/dev', '--proc', '/proc',
                             '--tmpfs', '/run', '--tmpfs', '/tmp', '--clearenv',
                             '--setenv', 'PATH', '/usr/sbin:/usr/bin:/sbin:/bin', '--setenv', 'LC_ALL', 'C',
                             '--', 'python3', '-c', code], capture_output=True, text=True)
    if result.returncode:
        raise ValueError('Actual shutdown restore failed: ' + result.stdout + result.stderr)


def check_root_hostname(tree):
    """Exercise naming using only the shipped root's Python and libraries."""
    sysroot = tree.parent / 'root-tree'
    code = ('import runpy; m=runpy.run_path("/usr/lib/gpu-root/initialize-identity.py"); '
            'f=m["runtime_hostname"]; h="01234567-89ab-cdef-0123-456789abcdef"; '
            'assert f(h,"LLM-PC-01","gpu-112233445566")=="llm-pc-01-0123456789abcdef0123456789abcdef"; '
            'assert f(h,None,"gpu-112233445566")=="gpu-112233445566"; '
            'assert len(f(h,"a"*255,"gpu-112233445566"))==63; '
            'print("Root hostname policy passed")')
    result = subprocess.run(['bwrap', '--unshare-all', '--ro-bind', str(sysroot), '/',
                             '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/tmp',
                             '--clearenv', '--setenv', 'PATH', '/usr/sbin:/usr/bin:/sbin:/bin',
                             '--', 'python3', '-c', code], capture_output=True, text=True)
    if result.returncode:
        raise ValueError('Root hostname policy failed: ' + result.stderr.strip())


def cpio_entries(path):
    data = path.read_bytes()
    offset = 0
    entries = {}
    while offset < len(data):
        while offset < len(data) and data[offset] == 0:
            offset += 1
        if offset == len(data):
            break
        if data[offset:offset+6] not in (b"070701", b"070702"):
            raise ValueError(f"Unsupported initramfs cpio header at {offset}")
        fields = [int(data[offset+6+8*i:offset+14+8*i], 16) for i in range(13)]
        size, namesize = fields[6], fields[11]
        name = data[offset+110:offset+110+namesize-1].decode()
        begin = (offset + 110 + namesize + 3) & ~3
        if begin + size > len(data):
            raise ValueError("Truncated cpio entry")
        if name != "TRAILER!!!":
            entries[name.removeprefix("./")] = (begin, size)
        offset = (begin + size + 3) & ~3
    return data, entries


def check_initramfs_runtime(tree):
    """Import Python against only the files actually shipped in the initramfs."""
    tree = tree.resolve()
    sysroot = tree.parent / 'root-tree'
    if not (sysroot / 'usr/bin/cpio').is_file():
        raise ValueError('Generated root-tree with cpio is required for isolated initramfs validation')
    with tempfile.TemporaryDirectory(prefix='initramfs-runtime-', dir=tree.parent) as folder:
        subprocess.run(['bwrap', '--unshare-all', '--uid', '0', '--gid', '0', '--die-with-parent',
                        '--ro-bind', str(sysroot), '/', '--bind', folder, '/out',
                        '--ro-bind', str(tree), '/work', '--dev', '/dev', '--proc', '/proc',
                        '--tmpfs', '/tmp', '--clearenv', '--setenv', 'PATH', '/usr/sbin:/usr/bin:/sbin:/bin',
                        '--', 'sh', '-c', 'cd /out && cpio --quiet -id --no-preserve-owner --no-absolute-filenames < /work/boot/initramfs.img'],
                       check=True, capture_output=True)
        code = ('import ctypes,fcntl,hashlib,json,importlib.util,io; '
                'k=ctypes.CDLL("libkmod.so.2"); '
                'k.kmod_new.restype=ctypes.c_void_p; k.kmod_new.argtypes=[ctypes.c_char_p,ctypes.c_void_p]; '
                'context=k.kmod_new(None,None); assert context, "libkmod context failed"; '
                'k.kmod_unref.argtypes=[ctypes.c_void_p]; k.kmod_unref(context); '
                's=importlib.util.spec_from_file_location("gpu_root", "/usr/lib/gpu-root.py"); '
                'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.check_volume("ntfs",0); '
                'out=io.StringIO(); p=m.ImageProgress("Initramfs progress check",1,out); '
                'step=p.step("Copying fixture",1); step.__enter__(); '
                'm.copy_image(io.BytesIO(b"x"),io.BytesIO(),p); step.__exit__(None,None,None); '
                'assert "[step 1/1]" in out.getvalue() and "100%" in out.getvalue(); '
                's=importlib.util.spec_from_file_location("fleet_registry", "/usr/lib/fleet-registry.py"); '
                'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                'assert m.computer_name("LLM-PC-01")=="LLM-PC-01"; '
                's=importlib.util.spec_from_file_location("ntfs_hibernation", "/usr/lib/ntfs-hibernation.py"); '
                'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); ctypes.CDLL(m.LIBRARY); '
                's=importlib.util.spec_from_file_location("detach_loop", "/usr/lib/detach-loop.py"); '
                'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                's=importlib.util.spec_from_file_location("gpu_diag", "/usr/lib/gpu-diagnostic.py"); '
                'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                'm.guard("gpu_root.diagnostic=1 gpu_root.allow_ntfs_write=0"); '
                's=importlib.util.spec_from_file_location("manual_unlock", "/usr/lib/gpu-unlock-diagnostic.py"); '
                'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                'm.preconditions({"hardware_uuid":"fixture", "gpu_bdf":"0000:01:00.0"}, "boot", "fixture", False, False, "", False, True); '
                'print("Isolated initramfs imports passed")')
        result = subprocess.run(['bwrap', '--unshare-all', '--uid', '0', '--gid', '0', '--die-with-parent',
                                 '--ro-bind', folder, '/', '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/run',
                                 '--tmpfs', '/tmp', '--clearenv', '--setenv', 'PATH', '/usr/sbin:/usr/bin:/sbin:/bin',
                                 '--', 'python3', '-c', code], capture_output=True, text=True)
        if result.returncode:
            raise ValueError('Isolated initramfs Python imports failed: ' + result.stderr.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tree", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diagnostic", action="store_true")
    args = parser.parse_args()
    tree = args.tree
    check_host_gpu_addresses(tree)
    check_initramfs_runtime(tree)
    check_shutdown_restore(tree)
    check_root_hostname(tree)
    master = tree / "gpu-linux/root.img"
    manifest = json.loads((tree / "gpu-linux/master.json").read_text())
    with master.open("rb") as source:
        sha = hashlib.file_digest(source, "sha256").hexdigest()
    if sha != manifest["sha256"] or master.stat().st_size != manifest["size"]:
        raise ValueError("Master hash/size mismatch")
    subprocess.run(["e2fsck", "-f", "-n", str(master)], check=True, capture_output=True)
    def debug(command):
        return subprocess.check_output(["debugfs", "-R", command, str(master)], text=True, stderr=subprocess.DEVNULL)
    inside = json.loads(debug("cat /usr/lib/gpu-root/image.json"))
    network = debug('cat /etc/systemd/network/20-gpu-ethernet.network')
    if network.count('UseHostname=no') != 2 or '[DHCPv4]' not in network or '[DHCPv6]' not in network:
        raise ValueError('DHCP hostname override guard missing')
    archive = '/boot/initramfs-' + manifest['kernel_release'] + '.img'
    proc = subprocess.Popen(['debugfs', '-R', 'cat ' + archive, str(master)],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    embedded = hashlib.sha256()
    while block := proc.stdout.read(8 * 1024**2):
        embedded.update(block)
    if proc.wait() or embedded.hexdigest() != digest(tree / 'boot/initramfs.img'):
        raise ValueError('Root embedded shutdown archive differs from the USB initramfs')
    if inside["version"] != manifest["version"] or inside["kernel_release"] != manifest["kernel_release"]:
        raise ValueError("Master filesystem release differs from external manifest")
    if "User:     0" not in debug("stat /etc/passwd"):
        raise ValueError("Master root ownership is incorrect")
    if "Size: 0" not in debug("stat /etc/machine-id"):
        raise ValueError("Master contains a machine ID")
    if "File not found" not in subprocess.run(["debugfs", "-R", "stat /etc/ssh/ssh_host_ed25519_key", str(master)],
                                              capture_output=True, text=True).stderr:
        raise ValueError("Master contains a shared SSH host key")
    model_listing = debug("ls -l /models")
    if any(".gguf" in line.lower() for line in model_listing.splitlines()):
        raise ValueError("Master unexpectedly contains models")
    if not manifest.get('persistent_state'):
        cache_stat = debug("stat /home/gpu/.llamacpp")
        if "Type: directory" not in cache_stat or "User:  1000" not in cache_stat:
            raise ValueError("Writable per-host llama.cpp cache is missing or has wrong owner")
        if any(".gguf" in line.lower() for line in debug("ls -l /home/gpu/.llamacpp").splitlines()):
            raise ValueError("Master unexpectedly contains cached models")
    if 'project_version' in manifest:
        info = json.loads(debug('cat /usr/lib/gpu-root/image.json'))
        for key in ('version', 'project_version', 'root_revision', 'boot_abi', 'state_schema', 'registry_schema', 'components'):
            if info.get(key) != manifest.get(key):
                raise ValueError('Root release metadata differs from master manifest: ' + key)
        for name in ('boot-banner.py', 'release-info.py', 'initialize-identity.py'):
            if debug('cat /usr/lib/gpu-root/' + name).strip() != (Path(__file__).resolve().parents[1] / 'runtime' / name).read_text().strip():
                raise ValueError('Obsolete root release presentation: ' + name)
    data, entries = cpio_entries(tree / "boot/initramfs.img")
    for name, source in (
            ('usr/lib/detach-loop.py', 'runtime/detach-loop.py'),
            ('var/lib/dracut/hooks/shutdown/10-shutdown-root.sh', 'runtime/dracut/95gpu-root/shutdown-root.sh')):
        if name not in entries:
            raise ValueError('Shutdown storage helper missing from initramfs: ' + name)
        offset, size = entries[name]
        if data[offset:offset+size] != (Path(__file__).resolve().parents[1] / source).read_bytes():
            raise ValueError('Obsolete shutdown storage helper: ' + name)
    required = ["usr/bin/python3.13", "usr/bin/systemctl", "usr/bin/ntfs-3g", "usr/bin/ntfs-3g.probe", "usr/lib/gpu-root.py", "shutdown",
                "usr/lib/ntfs-hibernation.py", "usr/lib/x86_64-linux-gnu/libntfs-3g.so.89",
                "usr/lib/systemd/system/dracut-cmdline.service",
                "usr/lib/systemd/system/dracut-pre-udev.service",
                "usr/lib/systemd/system/dracut-pre-mount.service",
                "usr/lib/systemd/system-generators/dracut-rootfs-generator",
                "etc/systemd/system/initrd.target.wants/dracut-pre-mount.service",
                "etc/systemd/system/initrd.target.wants/dracut-pre-udev.service",
                "usr/lib/dracut/need-initqueue",
                "var/lib/dracut/hooks/pre-mount/20-mount-root.sh"]
    for name in required:
        if name not in entries:
            raise ValueError(f"Missing initramfs item: {name}")
    if "var/lib/dracut/hooks/mount/20-mount-root.sh" in entries:
        raise ValueError("Root setup is in the late dracut mount hook")
    pre_mount = entries["usr/lib/systemd/system/dracut-pre-mount.service"]
    unit = data[pre_mount[0]:pre_mount[0]+pre_mount[1]]
    if b"Before=initrd-root-fs.target sysroot.mount" not in unit:
        raise ValueError("Pre-mount service is not ordered before sysroot.mount")
    suffixes = ["/gpu_unlock.ko", "/ext4.ko", "/loop.ko", "01-early-unlock.sh", "10-shutdown-root.sh"]
    for suffix in suffixes:
        if not any(name.endswith(suffix) or name.endswith(suffix + ".xz") or name.endswith(suffix + ".zst") for name in entries):
            raise ValueError(f"Missing initramfs module/hook: {suffix}")
    if manifest.get('shared_registry'):
        if manifest.get('automatic_root_recovery'):
            name = 'usr/lib/root-maintenance.py'
            if name not in entries:
                raise ValueError('Automatic recovery helper missing from initramfs')
            offset, size = entries[name]
            if data[offset:offset+size] != (Path(__file__).resolve().parents[1] / 'runtime/root-maintenance.py').read_bytes():
                raise ValueError('Automatic recovery helper differs from source')
        if 'usr/lib/fleet-registry.py' not in entries:
            raise ValueError('Shared registry runtime missing from initramfs')
        offset, size = entries['usr/lib/fleet-registry.py']
        if data[offset:offset+size] != (Path(__file__).resolve().parents[1] / 'runtime/fleet-registry.py').read_bytes():
            raise ValueError('Shared registry runtime differs from source')
        if 'usr/lib/storage-target.py' not in entries:
            raise ValueError('USB storage policy missing from initramfs')
        offset, size = entries['usr/lib/storage-target.py']
        if data[offset:offset+size] != (Path(__file__).resolve().parents[1] / 'runtime/storage-target.py').read_bytes():
            raise ValueError('USB storage policy differs from source')
        for module in ('/vfat.ko', '/fat.ko'):
            if not any(module in name for name in entries):
                raise ValueError('Missing configuration FAT driver: ' + module)
        if not args.diagnostic and 'load_env --file=' not in (tree / 'boot/grub/grub.cfg').read_text():
            raise ValueError('Shared registry boot selection missing')
    runtime_entry = entries["usr/lib/gpu-root.py"]
    controller = data[runtime_entry[0]:runtime_entry[0]+runtime_entry[1]]
    if controller != (Path(__file__).resolve().parents[1] / "runtime/gpu-root.py").read_bytes():
        raise ValueError("Initramfs contains an obsolete controller")
    offset, size = entries['usr/lib/ntfs-hibernation.py']
    if data[offset:offset+size] != (Path(__file__).resolve().parents[1] / 'runtime/ntfs-hibernation.py').read_bytes():
        raise ValueError('Initramfs hibernation inspector differs from source')
    offset, size = entries['var/lib/dracut/hooks/pre-mount/20-mount-root.sh']
    if data[offset:offset+size] != (Path(__file__).resolve().parents[1] /
                                  'runtime/dracut/95gpu-root/mount-root.sh').read_bytes():
        raise ValueError('Initramfs console progress hook differs from source')
    for name in ("booter_load_ga102.bin", "gsp_ga10x.bin", "gsp_boot_ga102.bin", "gsp_boot_desc_ga102.bin", "v67-ga102-610.43.03.bin"):
        if not any(path.endswith("/gpu-unlock/610.43.03/" + name) for path in entries):
            raise ValueError(f"Missing firmware: {name}")
    if not any("_ctypes" in path and path.endswith(".so") for path in entries):
        raise ValueError("Python ctypes extension missing")
    menu_text = (tree / "boot/grub/grub.cfg").read_text()
    kernel_lines = [line.strip() for line in menu_text.splitlines()
                    if line.strip().startswith("linux ")]
    if args.diagnostic:
        if (len(kernel_lines) != 1 or "gpu_root.allow_ntfs_write=0 gpu_root.diagnostic=1" not in kernel_lines[0]
                or "gpu_unlock.targets=" in menu_text or "set default=0\n" not in menu_text
                or "Read-only USB/input diagnostic" not in menu_text
                or "var/lib/dracut/hooks/pre-trigger/05-diagnostic.sh" not in entries):
            raise ValueError("Diagnostic boot could reach unlock or writable NTFS")
        diagnostic = entries["var/lib/dracut/hooks/pre-trigger/05-diagnostic.sh"]
        hook = data[diagnostic[0]:diagnostic[0]+diagnostic[1]]
        if hook != (Path(__file__).resolve().parents[1] / "runtime/dracut/95gpu-root/diagnostic.sh").read_bytes():
            raise ValueError("Diagnostic hook in initramfs differs from reviewed source")
        for module in ("usbhid.ko", "hid-generic.ko", "xhci-pci.ko", "usb-storage.ko"):
            if not any(module in name for name in entries):
                raise ValueError(f"Missing diagnostic USB/input module: {module}")
        for path in ("usr/lib/gpu-diagnostic.py", "usr/bin/gpu-diag-nvidia", "usr/bin/gpu-diag-finish",
                     "usr/lib/gpu-unlock-diagnostic.py", "usr/bin/gpu-diag-unlock",
                     "usr/sbin/sulogin", "usr/bin/setsid", "usr/bin/ntfsinfo", "usr/bin/lsblk",
                     "usr/sbin/chroot", "usr/bin/tee",
                     "etc/systemd/system/dracut-pre-trigger.service.d/90-diagnostic.conf"):
            if path not in entries:
                raise ValueError(f"Missing interactive diagnostic item: {path}")
        offset, size = entries["usr/lib/gpu-diagnostic.py"]
        if data[offset:offset+size] != (Path(__file__).resolve().parents[1] / "runtime/gpu-diagnostic.py").read_bytes():
            raise ValueError("Diagnostic collector differs from reviewed source")
    else:
        if "set default=1\n" not in menu_text or menu_text.index('menuentry "Normal / baseline"') > menu_text.index('menuentry "gpu-unlock enabled"'):
            raise ValueError("gpu-unlock menu entry is not the default")
        if len(kernel_lines) != (3 if manifest.get("shared_registry") else 2) or kernel_lines[1] != kernel_lines[0] + " gpu_unlock.targets=$gpu_targets":
            raise ValueError("Boot entries differ beyond targets")
        if manifest.get('shared_registry'):
            if 'gpu_root.no_gpu_test=1' not in kernel_lines[2] or 'gpu_unlock.targets=' in kernel_lines[2]:
                raise ValueError('Invalid explicit no-GPU test entry')
            for name in ('no-gpu-test.py', 'fleet-registry.py', 'release-info.py', 'replace-root.py'):
                offset, size = entries['usr/lib/' + name]
                if data[offset:offset+size] != (Path(__file__).resolve().parents[1] / 'runtime' / name).read_bytes():
                    raise ValueError('Obsolete initramfs test policy: ' + name)
    efi = (tree / "EFI/BOOT/BOOTX64.EFI").read_bytes()
    if efi[:2] != b"MZ" or efi[struct.unpack_from("<I", efi, 60)[0]:][:4] != b"PE\0\0":
        raise ValueError("UEFI payload is not a PE image")
    check_mokmanager(tree)
    pilot_checked = []
    if inside.get("usb_ready_message_staged"):
        for path in ("/usr/lib/gpu-root/announce-usb-ready.py",
                     "/usr/lib/systemd/system/gpu-root-usb-ready.service",
                     "/etc/systemd/system/multi-user.target.wants/gpu-root-usb-ready.service"):
            if "Type:" not in debug(f"stat {path}"):
                raise ValueError(f"USB ready announcement component missing: {path}")
    if inside.get("local_maintenance_console"):
        for path in ("/usr/lib/systemd/system/gpu-root-console.service", "/usr/local/bin/gpu-pair",
                     "/etc/systemd/system/multi-user.target.wants/gpu-root-console.service"):
            if "Type:" not in debug(f"stat {path}"):
                raise ValueError(f"Local maintenance component missing: {path}")
    if inside.get('local_pair_menu'):
        if inside['local_pair_menu'] != 'startup-and-exit-v1':
            raise ValueError('Unknown local PAIR menu version')
        if 'Type: regular' not in debug('stat /usr/lib/gpu-root/pair-console.py'):
            raise ValueError('Local PAIR menu supervisor is missing')
        pair_unit = debug('cat /usr/lib/systemd/system/gpu-pair.service')
        if 'ExecStart=/usr/bin/python3 /usr/lib/gpu-root/pair-console.py' not in pair_unit:
            raise ValueError('PAIR service does not launch the local menu')
        if 'KillSignal=SIGHUP' not in debug('cat /usr/lib/systemd/system/gpu-root-console.service'):
            raise ValueError('Maintenance console lacks graceful shutdown signal')
    pair_runtime_checked = False
    if manifest.get('persistent_state'):
        meta = manifest['persistent_state']
        state = tree / 'gpu-linux/state.img'
        if (state.is_symlink() or meta.get('schema') != 1 or state.stat().st_size != meta['size']
                or digest(state) != meta['sha256'] or inside.get('state_schema') != 1):
            raise ValueError('Persistent state template metadata differs')
        subprocess.run(['e2fsck', '-f', '-n', str(state)], check=True, capture_output=True)
        def state_debug(command):
            return subprocess.check_output(['debugfs', '-R', command, str(state)], text=True, stderr=subprocess.DEVNULL)
        if json.loads(state_debug('cat /layout.json')) != {'schema': 1, 'kind': 'pair-boot-state'}:
            raise ValueError('State layout mismatch')
        if state_debug('cat /system/machine-id').strip() or state_debug('cat /system/hostname').strip():
            raise ValueError('State master contains host identity')
        if 'Type:' in state_debug('stat /system/gpu-root/identity.json'):
            raise ValueError('State master contains identity record')
        for path in ('/home/gpu', '/cache/gpu-inference'):
            if 'User:  1000' not in state_debug('stat ' + path):
                raise ValueError('State user directory ownership mismatch')
        app = '/home/gpu/.config/Nvidia Corporation/Personal AI Router'
        override = json.loads(state_debug('cat ' + json.dumps(app + '/engines/llamacpp.json')))
        platform = override['platforms']['linux/amd64']
        if platform['detect'] != ['/opt/llama.cpp/bin/llama-server'] or platform['runtime']['bin'] != '/opt/llama.cpp/bin/llama-server':
            raise ValueError('State does not select root-owned engine')
        if 'Type:' in state_debug('stat ' + json.dumps(app + '/engine-bin/llamacpp/llama-server')):
            raise ValueError('State template contains engine executable')
        if json.loads(state_debug('cat ' + json.dumps(app + '/engine-bin/engine-state.json'))) != {'engines': {'llamacpp': True}}:
            raise ValueError('State engine intent seed missing')
        for helper in ('persistent-state.py', 'replace-root.py'):
            entry = entries.get('usr/lib/' + helper)
            if entry is None or data[entry[0]:entry[0]+entry[1]] != (Path(__file__).resolve().parents[1] / 'runtime' / helper).read_bytes():
                raise ValueError('State/update initramfs helper missing or stale')
        for helper in ('boot-banner.py', 'pair-session.py'):
            if 'Type: regular' not in debug('stat /usr/lib/gpu-root/' + helper):
                raise ValueError('PAIR console helper missing: ' + helper)
        if 'Type: regular' not in debug('stat /usr/bin/systemd-run'):
            raise ValueError('Transient session launcher missing')
        pair_runtime_checked = True
    if manifest['version'] == '0.8.0-shared':
        if manifest.get('automatic_root_recovery') is not True:
            raise ValueError('Automatic root recovery policy is missing')
        if 'migration' in manifest:
            raise ValueError('New installations must not depend on a legacy root migration')
    if manifest['version'] in ('0.7.8-shared', '0.8.0-shared'):
        app = '/home/gpu/.config/Nvidia Corporation/Personal AI Router'
        for path in ('/usr/bin/gzip', '/etc/ssl/certs/ca-certificates.crt',
                     '/etc/pam.d/common-auth', '/etc/pam.d/common-account',
                     '/etc/pam.d/common-password', '/etc/pam.d/common-session',
                     '/usr/lib/systemd/system/gpu-pair.service',
                     '/etc/systemd/system/multi-user.target.wants/gpu-pair.service',
                     app + '/engine-bin/llamacpp/llama-server',
                     app + '/engine-bin/llamacpp/libggml-cuda.so'):
            if 'Type:' not in debug('stat ' + json.dumps(path)):
                raise ValueError('PAIR runtime component missing: ' + path)
        override = json.loads(debug('cat ' + json.dumps(app + '/engines/llamacpp.json')))
        if override['platforms']['linux/amd64']['runtime']['launch_args'] != ['--models-dir', '/models']:
            raise ValueError('PAIR models directory defaults differ')
        desired = json.loads(debug('cat ' + json.dumps(app + '/engine-bin/engine-state.json')))
        if desired != {'engines': {'llamacpp': True}}:
            raise ValueError('PAIR engine autostart is not seeded')
        if 'setpriv --reuid=gpu --regid=gpu --init-groups' not in debug('cat /usr/local/bin/gpu-pair'):
            raise ValueError('PAIR launcher does not use tested privilege drop')
        if 'TTYPath=/dev/tty2' not in debug('cat /usr/lib/systemd/system/gpu-root-console.service'):
            raise ValueError('Maintenance console conflicts with PAIR tty1')
        pair_runtime_checked = True
    if "pilot_components" in inside:
        pilot = inside["pilot_components"]
        if pilot.get("kernel_release") != manifest["kernel_release"] or pilot.get("nvidia_driver_version") != "615.71.10":
            raise ValueError("Pilot component versions differ from image/kernel")
        pilot_paths = ("/usr/bin/nvidia-smi", "/usr/lib/x86_64-linux-gnu/libcuda.so.1",
                       f"/usr/lib/modules/{manifest['kernel_release']}/updates/dkms/nvidia.ko",
                       "/opt/nvpair/bin/nvpair-tui", "/opt/nvpair/bin/nvpair-ui-broker",
                       "/opt/llama.cpp/bin/llama-server", "/opt/llama.cpp/bin/libggml-cuda.so",
                       "/usr/lib/gpu-root/pilot-components.json")
        if inside.get("cuda_runtime_staged") == "bundled-cublas-12.8":
            pilot_paths += ("/opt/llama.cpp/bin/libcudart.so.12",
                            "/opt/llama.cpp/bin/libcublas.so.12",
                            "/opt/llama.cpp/bin/libcublasLt.so.12")
        for path in pilot_paths:
            if "Type:" not in debug(f"stat {path}"):
                raise ValueError(f"Pilot component missing from master: {path}")
            pilot_checked.append(path)
    report = {"schema": 1, "master_sha256": sha, "master_size": master.stat().st_size,
              "image": inside, "initramfs_entries": len(entries), "required_entries_checked": required,
              "pilot_components_checked": pilot_checked, "pair_runtime_checked": pair_runtime_checked,
              "master_identity_empty": True, "shared_ssh_keys_absent": True,
              "boot_entries_only_targets_differ": not args.diagnostic and not manifest.get('shared_registry'),
              "normal_unlock_only_targets_differ": not args.diagnostic,
              "explicit_no_gpu_test_entry": not args.diagnostic and bool(manifest.get('shared_registry')),
              "read_only_diagnostic": args.diagnostic,
              "uefi_pe_present": True,
              "native_boot_tested": False, "ntfs_mount_and_shutdown_tested": False,
              "gpu_measurements_performed": False}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f"Artifact validation failed: {error}")
