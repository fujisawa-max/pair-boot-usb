#!/usr/bin/env python3
"""Build ext4 master + dracut initramfs + UEFI tree in build/ only."""
import argparse
import hashlib
import importlib.util
import json
import os
import runpy
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
RELEASE = runpy.run_path(str(ROOT / "scripts/release-metadata.py"))["metadata"]()
KERNEL = RELEASE["components"]["Kernel"]


def run(*args, **kwargs):
    subprocess.run(list(map(str, args)), check=True, **kwargs)


def write(stage, path, value):
    destination = stage / path.lstrip("/")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(value)
    return destination


def symlink(stage, path, target):
    destination = stage / path.lstrip("/")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        destination.unlink()
    elif destination.exists():
        raise ValueError(f"Refusing to replace ordinary file {destination}")
    destination.symlink_to(target)


def require_empty_home(source):
    for relative in ('home', 'home/gpu'):
        path = source / relative
        if path.is_symlink():
            raise ValueError('Source user home must not be linked')
    home = source / 'home/gpu'
    if home.exists() and (not home.is_dir() or any(home.iterdir())):
        raise ValueError('Source user home must be empty; user state is not a build input')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT / "build/appliance-root")
    parser.add_argument("--output", type=Path, default=ROOT / "build/appliance-v0.2.0")
    parser.add_argument("--version", default=RELEASE["version"])
    parser.add_argument("--state-size-mib", type=int, default=1024)
    parser.add_argument("--size-mib", type=int, default=2048)
    parser.add_argument("--profiles", type=Path, default=ROOT / "config/gpu-profiles.example.json")
    parser.add_argument("--hosts", type=Path, help="reviewed Windows inventory JSON directory")
    parser.add_argument("--signed-modules", type=Path, help="verified production bundle; also selects Debian signed EFI")
    parser.add_argument("--efi-root", type=Path, help="extracted hash-verified signed shim/GRUB tree below build/")
    parser.add_argument("--shared-registry", action="store_true", help="read same-USB GPU-LOGS external fleet registry")
    parser.add_argument("--diagnostic", action="store_true", help="single baseline entry; display USB/input state without NTFS writes")
    parser.add_argument("--local-console", action="store_true", help="experimental appliance: physical root console without a password")
    parser.add_argument("--network-overlay", type=Path, help="verified candidate-network overlay under build/")
    parser.add_argument("--storage-directory", default="GPU-Linux", help="single safe folder on selected NTFS volume")
    parser.add_argument("--fedora-tree", type=Path, help="verified Fedora Live tree under build/")
    parser.add_argument("--pair-runtime", action="store_true", help="stage verified PAIR-managed backend, CA/gzip/PAM and models defaults")
    args = parser.parse_args()
    if not 64 <= args.state_size_mib <= 8192:
        parser.error("state size must be 64-8192 MiB")
    storage_spec = importlib.util.spec_from_file_location('storage_policy', ROOT / 'runtime/fleet-registry.py')
    storage_policy = importlib.util.module_from_spec(storage_spec)
    storage_spec.loader.exec_module(storage_policy)
    try:
        storage_policy.validate_storage_directory(args.storage_directory)
    except ValueError as error:
        parser.error(str(error))
    if args.diagnostic and args.local_console:
        parser.error("diagnostic and local-console are separate boot modes")
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to((ROOT / "build").resolve()) or output == ROOT / "build":
        parser.error("output must be a fresh directory below build/")
    if args.size_mib < 1024:
        parser.error("size must be >=1024 MiB")
    if not args.version or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in args.version):
        parser.error("invalid version")
    source_root = args.source_root.resolve()
    if not source_root.is_relative_to((ROOT / "build").resolve()) or not (source_root / "usr/lib/os-release").is_file():
        parser.error("source-root must be an extracted Debian build root below build/")
    for helper in ("usr/bin/ldd", "usr/sbin/ldconfig"):
        if not (source_root / helper).is_file():
            parser.error(f"source-root lacks {helper}; dracut cannot resolve initramfs libraries")
    signed = None
    if args.signed_modules:
        if not args.efi_root or not args.efi_root.resolve().is_relative_to(ROOT / 'build'):
            parser.error('signed modules require an explicit --efi-root below build/')
        spec = importlib.util.spec_from_file_location("signed_bundle", ROOT / "scripts/verify-signed-module-bundle.py")
        signed_bundle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(signed_bundle)
        try:
            signed = signed_bundle.verify(args.signed_modules)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            parser.error(f"signed module bundle rejected before build: {error}")
        review = args.efi_root.resolve() / 'usr/lib'
        for item in (review / "shim/mmx64.efi.signed",
                     review / "shim/shimx64.efi.signed",
                     review / "grub/x86_64-efi-signed/grubx64.efi.signed"):
            if not item.is_file():
                parser.error(f"missing Debian signed EFI input: {item}")
    profiles = json.loads(args.profiles.read_text())
    reviewed_hosts = []
    if args.hosts:
        spec = importlib.util.spec_from_file_location("host_inventory", ROOT / "scripts/check-host-inventory.py")
        inventory_check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inventory_check)
        try:
            reviewed_hosts = inventory_check.inspect_directory(args.hosts, profiles, signed_boot=bool(signed))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            parser.error(f"host inventory rejected before build: {error}")
    stage = output / "root-tree"
    output.mkdir(parents=True)
    copied = 0
    def copy_stage_file(source, destination):
        nonlocal copied
        result = shutil.copy2(source, destination)
        copied += 1
        if copied % 1000 == 0:
            print(f"Staged {copied} input files", flush=True)
        return result
    require_empty_home(args.source_root)
    shutil.copytree(args.source_root, stage, symlinks=True, copy_function=copy_stage_file)
    if args.network_overlay:
        overlay = args.network_overlay.resolve()
        if not overlay.is_relative_to((ROOT / "build").resolve()):
            raise ValueError("Network overlay must be below build/")
        locked = json.loads((ROOT / "inputs/candidate-network-inputs.json").read_text())
        if json.loads((overlay / "candidate-network-inputs.json").read_text()) != locked:
            raise ValueError("Network overlay input lock mismatch")
        shutil.copytree(overlay, stage, symlinks=True, dirs_exist_ok=True)
    # All changes below affect this generated staging tree only.
    for path in ("dev", "proc", "sys", "run", "tmp", "work", "out", "models", "home/gpu", "root", "usr/local/bin",
                 "persist", "var/lib/gpu-root", "var/lib/gpu-bench", "var/cache/gpu-inference", "var/tmp/gpu-inference"):
        (stage / path).mkdir(parents=True, exist_ok=True)
    for path in ("tmp", "var/tmp"):
        (stage / path).chmod(0o1777)
    write(stage, "/etc/machine-id", "")
    write(stage, "/etc/hostname", "")
    write(stage, "/etc/hosts", "127.0.0.1 localhost\n::1 localhost ip6-localhost\n")
    write(stage, "/etc/fstab", "# Root loop and backing NTFS are managed by the initramfs; no Windows mounts here.\n")
    write(stage, "/etc/passwd", "root:x:0:0:root:/root:/bin/bash\ngpu:x:1000:1000:GPU user:/home/gpu:/bin/bash\nnobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n")
    write(stage, "/etc/group", "root:x:0:\ngpu:x:1000:\nnogroup:x:65534:\n")
    write(stage, "/etc/shadow", "root:!:20000:0:99999:7:::\ngpu:!:20000:0:99999:7:::\n").chmod(0o600)
    symlink(stage, "/var/lib/dbus/machine-id", "/etc/machine-id")
    for path in stage.glob("etc/ssh/ssh_host_*"):
        path.unlink()
    for path in (stage / "var/lib/systemd/random-seed", stage / "var/lib/gpu-root/identity.json"):
        path.unlink(missing_ok=True)
    # Reject inherited machine credentials/network profiles rather than copying them.
    for folder in (stage / "etc/NetworkManager/system-connections", stage / "etc/systemd/network"):
        if folder.exists() and any(folder.iterdir()):
            raise ValueError(f"Source has network profiles; sanitize explicitly: {folder}")
    write(stage, "/etc/systemd/network/20-gpu-ethernet.network", "[Match]\nName=en* eth*\n\n[Network]\nDHCP=yes\nLinkLocalAddressing=no\n")
    pilot_file = stage / "usr/lib/gpu-root/pilot-components.json"
    pilot = json.loads(pilot_file.read_text()) if pilot_file.is_file() else None
    if pilot and pilot.get("kernel_release") != KERNEL:
        raise ValueError("Pilot driver modules target a different kernel")
    if args.version != RELEASE["version"]:
        raise ValueError("Root build version must match config/release.json")
    image_info = {**RELEASE, "schema": 1, "version": args.version, "kernel_release": KERNEL,
                  "state_schema": 1, "gpu_unlock_commit": "af28cbbfd3d922a659227ecfb7369adfd924cf03",
                  "nvidia_driver_installed": bool(pilot), "torch_installed": False,
                  "status": "pilot-components-staged-not-hardware-qualified" if pilot else
                            "storage-and-benchmark-prototype-not-hardware-qualified"}
    if pilot:
        image_info["pilot_components"] = pilot
        image_info["cuda_runtime_staged"] = "bundled-cublas-12.8"
    if signed:
        image_info["module_signing_certificate_sha256"] = signed["certificate_sha256"]
        image_info["secure_boot_artifacts"] = "signed-shim-grub-kernel-and-six-gpu-modules-not-hardware-qualified"
    image_info["usb_ready_message_staged"] = True
    if args.local_console:
        image_info["local_maintenance_console"] = "passwordless physical tty2; PAIR on tty1; experimental candidate"
        image_info["local_pair_menu"] = "startup-and-exit-v1"
    if args.network_overlay:
        image_info["dns_service"] = "systemd-resolved with DHCP DNS and system D-Bus"
    write(stage, "/usr/lib/gpu-root/image.json", json.dumps(image_info, indent=2) + "\n")
    shutil.copy2(ROOT / "runtime/initialize-identity.py", stage / "usr/lib/gpu-root/initialize-identity.py")
    shutil.copy2(ROOT / "runtime/announce-usb-ready.py", stage / "usr/lib/gpu-root/announce-usb-ready.py")
    for helper in ("pair-console.py", "boot-banner.py", "pair-session.py", "release-info.py"):
        shutil.copy2(ROOT / "runtime" / helper, stage / "usr/lib/gpu-root" / helper)
    shutil.copy2(ROOT / "runtime/prepare-shutdown.py", stage / "usr/lib/gpu-root/prepare-shutdown.py")
    if args.pair_runtime:
        runpy.run_path(str(ROOT / "scripts/stage-pair-runtime.py"))["stage"](stage)
    # PAIR downloads into its user's llama.cpp cache. Keep it on the Linux root,
    # separate from the read-only Windows-managed model directory.

    shutil.copytree(ROOT / "config", stage / "usr/share/gpu-root/examples", dirs_exist_ok=True)
    shutil.copy2(ROOT / "runtime/llama-local.sh", stage / "usr/local/bin/llama-local.sh")
    (stage / "usr/local/bin/llama-local.sh").chmod(0o755)
    shutil.copy2(ROOT / "runtime/pair-local.sh", stage / "usr/local/bin/gpu-pair")
    (stage / "usr/local/bin/gpu-pair").chmod(0o755)
    for unit in (ROOT / "runtime/systemd").glob("*.service"):
        shutil.copy2(unit, stage / "usr/lib/systemd/system" / unit.name)
    for name in ("gpu-root-identity", "gpu-root-usb-ready", "systemd-networkd"):
        symlink(stage, f"/etc/systemd/system/multi-user.target.wants/{name}.service", f"/usr/lib/systemd/system/{name}.service")
    if args.network_overlay:
        symlink(stage, "/etc/systemd/system/multi-user.target.wants/systemd-resolved.service",
                "/usr/lib/systemd/system/systemd-resolved.service")
        symlink(stage, "/etc/systemd/system/sockets.target.wants/dbus.socket", "/usr/lib/systemd/system/dbus.socket")
        symlink(stage, "/etc/resolv.conf", "/run/systemd/resolve/stub-resolv.conf")
    if args.local_console:
        symlink(stage, "/etc/systemd/system/getty@tty1.service", "/dev/null")
        symlink(stage, "/etc/systemd/system/multi-user.target.wants/gpu-root-console.service",
                "/usr/lib/systemd/system/gpu-root-console.service")
        symlink(stage, "/etc/systemd/system/multi-user.target.wants/gpu-pair.service",
                "/usr/lib/systemd/system/gpu-pair.service")
    symlink(stage, "/etc/systemd/system/multi-user.target.requires/gpu-root-storage-guard.service",
            "/usr/lib/systemd/system/gpu-root-storage-guard.service")
    symlink(stage, "/etc/systemd/system/multi-user.target.wants/dracut-shutdown.service", "/usr/lib/systemd/system/dracut-shutdown.service")
    for unit in ("ssh.service", "systemd-networkd.service"):
        write(stage, f"/etc/systemd/system/{unit}.d/20-gpu-identity.conf",
              "[Unit]\nRequires=gpu-root-identity.service\nAfter=gpu-root-identity.service\n")
    # No network login or inference listeners until per-PC administrator provisioning.
    symlink(stage, "/etc/systemd/system/ssh.service", "/dev/null")
    symlink(stage, "/etc/systemd/system/suspend.target", "/dev/null")
    symlink(stage, "/etc/systemd/system/hibernate.target", "/dev/null")
    symlink(stage, "/etc/systemd/system/hybrid-sleep.target", "/dev/null")
    symlink(stage, "/etc/systemd/system/default.target", "/usr/lib/systemd/system/multi-user.target")
    module_dir = stage / f"usr/lib/modules/{KERNEL}/updates"
    module_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2((args.signed_modules / "gpu_unlock.ko") if signed else
                 (ROOT / f"build/modules/{KERNEL}/gpu_unlock.ko"), module_dir)
    if signed:
        nvidia_dir = module_dir / "dkms"
        for name in ("nvidia", "nvidia-modeset", "nvidia-drm", "nvidia-uvm", "nvidia-peermem"):
            destination = nvidia_dir / (name + ".ko")
            if not destination.is_file() or destination.is_symlink():
                raise ValueError(f"pilot source lacks ordinary NVIDIA module: {destination}")
            shutil.copy2(args.signed_modules / (name + ".ko"), destination)
    shutil.copytree(ROOT / "build/assets", stage / "usr/lib/firmware/gpu-unlock/610.43.03", dirs_exist_ok=True)
    run("depmod", "-b", stage, KERNEL)
    shutil.copytree(ROOT / "vendor/gpu-unlock/licenses", stage / "usr/share/licenses/gpu-unlock", dirs_exist_ok=True)
    shutil.copy2(ROOT / "vendor/gpu-unlock/firmware/NOTICE.md", stage / "usr/share/licenses/gpu-unlock/NOTICE.md")
    moddir = stage / "usr/lib/dracut/modules.d/95gpu-root"
    shutil.copytree(ROOT / "runtime/dracut/95gpu-root", moddir)
    shutil.copy2(ROOT / "runtime/gpu-root.py", moddir / "gpu-root.py")
    shutil.copy2(ROOT / "runtime/fleet-registry.py", moddir / "fleet-registry.py")
    shutil.copy2(ROOT / "runtime/storage-target.py", moddir / "storage-target.py")
    for helper in ("root-maintenance.py", "persistent-state.py", "replace-root.py", "no-gpu-test.py", "release-info.py", "detach-loop.py"):
        shutil.copy2(ROOT / "runtime" / helper, moddir / helper)
    shutil.copy2(ROOT / "runtime/gpu-diagnostic.py", moddir / "gpu-diagnostic.py")
    shutil.copy2(ROOT / "runtime/gpu-unlock-diagnostic.py", moddir / "gpu-unlock-diagnostic.py")
    for helper in ("diagnostic-nvidia.sh", "diagnostic-finish.sh", "diagnostic-unlock.sh"):
        shutil.copy2(ROOT / "runtime" / helper, moddir / helper)
    for path in moddir.glob("*.sh"):
        path.chmod(0o755)
    tree = output / "usb-tree"
    for path in ("boot", "EFI/BOOT", "gpu-linux/hosts"):
        (tree / path).mkdir(parents=True, exist_ok=True)
    command = ["bwrap", "--unshare-all", "--uid", "0", "--gid", "0", "--die-with-parent",
               "--bind", str(stage), "/", "--bind", str(tree), "/out", "--proc", "/proc", "--dev", "/dev",
               "--tmpfs", "/tmp", "--clearenv", "--setenv", "PATH", "/usr/sbin:/usr/bin:/sbin:/bin",
               "--setenv", "HOME", "/root", "--setenv", "LC_ALL", "C", "--"]
    # No host kernel discovery, host devices, installer or firmware configuration.
    run(*command, "dracut", "--tmpdir", "/tmp", "--no-hostonly", "--no-hostonly-cmdline", "--no-compress", "--reproducible", "--modules", "bash systemd systemd-initrd dracut-systemd kernel-modules gpu-root shutdown",
        "--omit", "network network-legacy network-manager crypt lvm mdraid resume", "/out/boot/initramfs.img", KERNEL)
    shutil.copy2(stage / f"boot/vmlinuz-{KERNEL}", tree / "boot/vmlinuz")
    shutil.copy2(tree / "boot/initramfs.img", stage / f"boot/initramfs-{KERNEL}.img")
    # sysusers can leave a generated locked gshadow unreadable after dracut.
    # fakeroot provides ownership metadata, not host CAP_DAC_OVERRIDE.
    for name in ("etc/shadow", "etc/gshadow"):
        path = stage / name
        if path.is_file():
            path.chmod(path.stat().st_mode | 0o400)
    data_uuid = str(uuid.uuid4())
    spec = importlib.util.spec_from_file_location("bootmenu", ROOT / "scripts/generate-boot-menu.py")
    menu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(menu)
    (tree / "boot/grub").mkdir()
    grub_text = menu.generate_diagnostic(data_uuid) if args.diagnostic else menu.generate(data_uuid, profiles, shared_registry=args.shared_registry)
    if args.local_console:
        grub_text = grub_text.replace("rd.auto=0 noresume", "rd.auto=0 noresume SYSTEMD_SULOGIN_FORCE=1")
        grub_text = grub_text.replace("set timeout=15", "set timeout=60", 1)
    if args.fedora_tree:
        fedora = args.fedora_tree.resolve()
        if not fedora.is_relative_to((ROOT / "build").resolve()):
            raise ValueError("Fedora tree must be below build/")
        info = json.loads((fedora / "fedora-manifest.json").read_text())
        if info['filesystem_uuid'] != 'F044-0107':
            raise ValueError("Unexpected Fedora filesystem identity")
        grub_text += menu.generate_fedora_entry(info['filesystem_uuid'])
    (tree / "boot/grub/grub.cfg").write_text(grub_text)
    if signed:
        review = args.efi_root.resolve() / 'usr/lib'
        shutil.copy2(review / "shim/shimx64.efi.signed", tree / "EFI/BOOT/BOOTX64.EFI")
        shutil.copy2(review / "shim/mmx64.efi.signed", tree / "EFI/BOOT/mmx64.efi")
        shutil.copy2(review / "grub/x86_64-efi-signed/grubx64.efi.signed", tree / "EFI/BOOT/grubx64.efi")
        (tree / "EFI/debian").mkdir()
        for path in (tree / "EFI/BOOT/grub.cfg", tree / "EFI/debian/grub.cfg"):
            shutil.copy2(tree / "boot/grub/grub.cfg", path)
        shutil.copy2(args.signed_modules / "signing-cert.der", tree / "EFI/BOOT/signing-cert.der")
    else:
        # The unsigned standalone EFI binary embeds the shared menu.
        run(*command, "grub-mkstandalone", "-O", "x86_64-efi", "--locales=", "--fonts=", "--modules=part_gpt ext2 smbios",
            "-o", "/out/EFI/BOOT/BOOTX64.EFI", "boot/grub/grub.cfg=/out/boot/grub/grub.cfg")
    if reviewed_hosts:
        for value in reviewed_hosts:
            value = {**value, "storage_directory": args.storage_directory}
            hardware = value["hardware_uuid"].lower()
            (tree / "gpu-linux/hosts" / (hardware + ".json")).write_text(json.dumps(value, indent=2) + "\n")
    # Seed state separately. The root contains no mutable user configuration.
    state_tree = output / 'state-tree'
    shutil.copytree(stage / 'home/gpu', state_tree / 'home/gpu')
    for child in (stage / 'home/gpu').iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for relative in ('system/gpu-root', 'cache/gpu-inference'):
        (state_tree / relative).mkdir(parents=True)
    for name in ('machine-id', 'hostname'):
        (state_tree / 'system' / name).write_text('')
    (state_tree / 'layout.json').write_text(json.dumps({'schema': 1, 'kind': 'pair-boot-state'}) + '\n')
    (state_tree / 'system').chmod(0o700)
    (state_tree / 'home/gpu').chmod(0o700)
    state_master = tree / 'gpu-linux/state.img'
    with state_master.open('xb') as stream:
        stream.truncate(args.state_size_mib * 1024**2)
    run('env', 'FAKEROOTDONTTRYCHOWN=1', 'fakeroot', 'sh', '-eu', '-c',
        'chown -hR 0:0 "$1"; chown -R 1000:1000 "$1/home/gpu" "$1/cache/gpu-inference"; '
        'mke2fs -q -t ext4 -F -L PAIR-STATE -d "$1" "$2"', 'state-build', state_tree, state_master)
    run('e2fsck', '-f', '-n', state_master)
    with state_master.open('rb') as stream:
        state_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    master = tree / "gpu-linux/root.img"
    with master.open("xb") as stream:
        stream.truncate(args.size_mib * 1024 * 1024)
    # fakeroot fixes image ownership without chowning real host files or requiring root.
    run("env", "FAKEROOTDONTTRYCHOWN=1", "fakeroot", "sh", "-eu", "-c",
        'chown -hR 0:0 "$1"; chown -R 1000:1000 "$1/home/gpu" "$1/var/cache/gpu-inference" '
        '"$1/var/tmp/gpu-inference" "$1/var/lib/gpu-bench"; '
        'mke2fs -q -t ext4 -F -L GPU-LINUX-ROOT -d "$1" "$2"', "build-root", stage, master)
    run("e2fsck", "-f", "-n", master)
    with master.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest = {**RELEASE, "schema": 1, "version": args.version, "kernel_release": KERNEL,
                "size": master.stat().st_size, "sha256": sha, "usb_data_uuid": data_uuid,
                "production_ready": False, "secure_boot_signed_artifacts": bool(signed),
                "shared_registry": args.shared_registry,
                "persistent_state": {"schema": 1, "filename": "state.img",
                                     "size": state_master.stat().st_size, "sha256": state_sha}}
    manifest['automatic_root_recovery'] = True
    state_master.chmod(0o444)
    (tree / "gpu-linux/master.json").write_text(json.dumps(manifest, indent=2) + "\n")
    master.chmod(0o444)
    (tree / "gpu-linux/master.json").chmod(0o444)
    (output / "build-report.json").write_text(json.dumps({"manifest": manifest, "image": image_info,
          "status": "file-artifacts-only; UEFI/NTFS/shutdown/GPU hardware qualification pending"}, indent=2) + "\n")
    print(f"Built ext4 master and {'signed' if signed else 'unsigned'} UEFI staging tree: {tree}")
    print("USB data partition must be ext4 with UUID " + data_uuid)
    print("No device was formatted, mounted, written or installed by this build utility.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"Appliance build failed: {error}")
