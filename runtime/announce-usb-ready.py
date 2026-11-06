#!/usr/bin/env python3
"""Show USB removal guidance only after all USB partitions are unmounted."""
import json
from pathlib import Path
import re
import subprocess
import sys

STATE = Path("/run/gpu-root/state.json")
CONSOLE = Path("/dev/console")


def output(*command):
    return subprocess.check_output(command, text=True).strip()


def mounted(nodes):
    for node in nodes:
        points = node.get("mountpoints")
        if points is None:
            points = []
        if not isinstance(points, list):
            raise ValueError("Cannot verify USB mount points")
        if any(point for point in points):
            return True
        if mounted(node.get("children", [])):
            return True
    return False


def verify(state, command=output):
    if state.get("usb_unmounted") is not True:
        raise ValueError("initramfs did not confirm USB unmount")
    if state.get('root_storage_on_boot_usb') is True:
        raise ValueError('root.img/Models are on this USB; keep it inserted until shutdown')
    device = state.get("usb_device", "")
    if not re.fullmatch(r"/dev/[A-Za-z0-9._+-]+", device):
        raise ValueError("invalid USB device in boot state")
    parent = command("lsblk", "-dn", "-o", "PKNAME", device)
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", parent):
        raise ValueError("USB parent device could not be identified")
    disk = "/dev/" + parent
    if command("lsblk", "-dn", "-o", "TRAN", disk).lower() != "usb":
        raise ValueError("boot device is not identified as USB transport")
    layout = json.loads(command("lsblk", "-J", "-o", "NAME,MOUNTPOINTS", disk))
    nodes = layout.get("blockdevices")
    if not isinstance(nodes, list) or len(nodes) != 1 or nodes[0].get("name") != parent:
        raise ValueError("USB mount inventory is incomplete")
    if mounted(nodes):
        raise ValueError("a USB partition is still mounted")


def main():
    try:
        state = json.loads(STATE.read_text())
        verify(state)
        message = ("\n*** USB CAN BE REMOVED NOW ***\n"
                   "The boot USB is no longer in use.\n"
                   "Reinsert this USB before the next boot.\n\n")
        if state.get('root_storage_transport') == 'usb':
            message += "Root storage is on another USB: keep that storage device inserted until shutdown.\n"
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        message = f"\n*** KEEP USB INSERTED ***\nUSB release was not verified: {error}\n\n"
    try:
        with CONSOLE.open("w") as console:
            console.write(message)
    except OSError as error:
        print(f"USB readiness message could not reach console: {error}", file=sys.stderr)
        return 1
    print(message.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
