#!/usr/bin/env python3
"""Generate two entries using identical kernel/initramfs and only targets differing."""
import argparse
import json
from pathlib import Path
import re
import sys


def generate_fedora_entry(uuid):
    if not re.fullmatch(r'[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}', uuid):
        raise ValueError('Invalid Fedora FAT UUID')
    return ('menuentry "Fedora Live / MOK and maintenance" {\n'
            '  insmod fat\n  insmod chain\n'
            f'  if search --no-floppy --fs-uuid --set=root {uuid}; then\n'
            '    echo "Loading Fedora shim from $root"\n'
            '    if chainloader /EFI/BOOT/BOOTX64.EFI; then\n'
            '      boot\n    fi\n'
            '  else\n    echo "Fedora Live partition was not found; stopping"\n'
            '  fi\n}\n')


def generate(usb_uuid, profiles, shared_registry=False):
    if not re.fullmatch(r"[A-Za-z0-9-]{4,64}", usb_uuid):
        raise ValueError("Invalid USB filesystem UUID")
    lines = ["set timeout=15", "set default=1", "insmod part_gpt", "insmod ext2", "insmod smbios",
             f"search --no-floppy --fs-uuid --set=root {usb_uuid}",
             "smbios --type 1 --get-uuid 8 --set=gpu_host_uuid", "set gpu_targets=unconfigured"]
    for hardware, bdf in sorted(({} if shared_registry else profiles).items()):
        if not re.fullmatch(r"[a-f0-9-]{36}", hardware) or not re.fullmatch(r"[a-f0-9]{4}:[a-f0-9]{2}:[a-f0-9]{2}\.[0-7]", bdf):
            raise ValueError("Invalid host UUID or GPU PCI address")
        lines += [f'if [ "$gpu_host_uuid" = "{hardware}" ]; then', f"  set gpu_targets={bdf}", "fi"]
    if shared_registry:
        # Read only the selected variable, never source executable configuration.
        lines += ["insmod fat", "insmod regexp", "insmod loadenv",
                  "set gpu_targets=unconfigured", "set gpu_default=1",
                  "if regexp --set=1:gpu_disk '^([^,]+),gpt2$' \"$root\"; then",
                  '  if [ -f "($gpu_disk,gpt3)/gpu-config/hosts/$gpu_host_uuid.env" ]; then',
                  '    load_env --file="($gpu_disk,gpt3)/gpu-config/hosts/$gpu_host_uuid.env" gpu_targets gpu_default',
                  '  fi', 'fi',
                  'if [ "$gpu_targets" = unsupported -o "$gpu_default" = 0 ]; then', '  set default=0', 'fi']
    common = (f"root=gpu-root gpu_root.usb_uuid={usb_uuid} gpu_root.allow_ntfs_write=1 "
              "rd.driver.blacklist=nouveau rd.auto=0 noresume")
    if shared_registry:
        common += " gpu_root.shared_registry=1"
    for title, extra in (("Normal / baseline", ""), ("gpu-unlock enabled", " gpu_unlock.targets=$gpu_targets")):
        lines += [f'menuentry "{title}" {{', f"  linux /boot/vmlinuz {common}{extra}",
                  "  initrd /boot/initramfs.img", "}"]
    if shared_registry:
        test_common = common.replace('rd.driver.blacklist=nouveau ', '')
        lines += ['menuentry "TEST / no NVIDIA GPU / USB storage only" {',
                  f'  linux /boot/vmlinuz {test_common} gpu_root.no_gpu_test=1 rd.driver.blacklist=nvidia,nvidia_drm,nvidia_modeset,nvidia_uvm,nouveau,gpu_unlock modprobe.blacklist=nvidia,nvidia_drm,nvidia_modeset,nvidia_uvm,nouveau,gpu_unlock',
                  '  initrd /boot/initramfs.img', '}']
    return "\n".join(lines) + "\n"


def generate_diagnostic(usb_uuid):
    if not re.fullmatch(r"[A-Za-z0-9-]{4,64}", usb_uuid):
        raise ValueError("Invalid USB filesystem UUID")
    return ("set timeout=15\nset default=0\ninsmod part_gpt\ninsmod ext2\n"
            f"search --no-floppy --fs-uuid --set=root {usb_uuid}\n"
            'menuentry "Read-only USB/input diagnostic (no GPU unlock)" {\n'
            f"  linux /boot/vmlinuz root=gpu-root gpu_root.usb_uuid={usb_uuid} "
            "gpu_root.allow_ntfs_write=0 gpu_root.diagnostic=1 "
            "rd.driver.blacklist=nouveau rd.auto=0 noresume\n"
            "  initrd /boot/initramfs.img\n}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usb-uuid", required=True)
    parser.add_argument("--profiles", type=Path, required=True, help="JSON mapping hardware UUID to GPU BDF")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        data = generate(args.usb_uuid, json.loads(args.profiles.read_text()))
        with args.output.open("x") as stream:
            stream.write(data)
    except (OSError, ValueError) as error:
        sys.exit(f"Boot menu generation failed: {error}")
