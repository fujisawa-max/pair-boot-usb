#!/usr/bin/env python3
"""Reject unsafe per-PC NTFS records before constructing a USB image."""
import argparse
import json
from pathlib import Path
import re
import sys
import time


def inspect_directory(directory, profiles, now=None, signed_boot=False):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("host inventory must be an ordinary directory")
    files = sorted(directory.glob("*.json"))
    if not files:
        raise ValueError("host inventory directory contains no JSON records")
    current = time.time() if now is None else now
    records = []
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"host inventory must be an ordinary file: {path.name}")
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or value.get("schema") != 1:
            raise ValueError(f"invalid host inventory schema: {path.name}")
        hardware_value = value.get("hardware_uuid", "")
        if not isinstance(hardware_value, str):
            raise ValueError(f"invalid DMI UUID: {path.name}")
        hardware = hardware_value.lower()
        if not re.fullmatch(r"[a-f0-9-]{36}", hardware) or path.name != hardware + ".json":
            raise ValueError(f"host inventory filename/UUID mismatch: {path.name}")
        if hardware in ("00000000-0000-0000-0000-000000000000",
                        "ffffffff-ffff-ffff-ffff-ffffffffffff"):
            raise ValueError(f"invalid DMI UUID: {path.name}")
        if hardware not in profiles:
            raise ValueError(f"no reviewed GPU PCI address for inventory: {hardware}")
        if not re.fullmatch(r"[A-Fa-f0-9]{16}", value.get("ntfs_uuid", "")):
            raise ValueError(f"invalid NTFS UUID: {path.name}")
        checked_at = value.get("checked_at_unix")
        if type(checked_at) not in (int, float):
            raise ValueError(f"invalid Windows safety inventory timestamp: {path.name}")
        age = current - checked_at
        if age < 0 or age > 30 * 86400:
            raise ValueError(f"Windows safety inventory expired or future dated: {path.name}")
        reasons = []
        if value.get("bitlocker_volume_status") != "FullyDecrypted" or value.get("bitlocker_protection") != "Off":
            reasons.append("BitLocker is encrypted, protected, or unknown")
        if value.get("fast_startup_enabled") is not False:
            reasons.append("Fast Startup is enabled or unknown")
        if value.get("secure_boot_enabled") is not False and not (signed_boot and value.get("secure_boot_enabled") is True):
            reasons.append("Secure Boot is enabled or unknown; current EFI and GPU modules are unsigned")
        if reasons:
            raise ValueError(f"unsafe Windows volume for {hardware}: {'; '.join(reasons)}")
        records.append({**value, "gpu_bdf": profiles[hardware]})
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hosts", type=Path, required=True)
    parser.add_argument("--profiles", type=Path, required=True)
    args = parser.parse_args()
    try:
        profiles = json.loads(args.profiles.read_text())
        records = inspect_directory(args.hosts, profiles)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        sys.exit(f"Host inventory rejected: {error}")
    print(f"Accepted {len(records)} reviewed host inventories for artifact construction")
    print("Native NTFS signature/probe and PCI BDF still require physical hardware checks.")


if __name__ == "__main__":
    main()
