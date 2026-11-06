#!/usr/bin/env python3
"""Verify kernel release and hashes of the five staged NVIDIA open modules."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "6.12.113+deb13-amd64"
VERSION = "615.71.10"
MODULES = ROOT / f"build/nvidia-modules/{VERSION}/{RELEASE}"
NAMES = ("nvidia", "nvidia-modeset", "nvidia-drm", "nvidia-uvm", "nvidia-peermem")


def main():
    records = []
    for name in NAMES:
        path = MODULES / f"{name}.ko"
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Missing regular NVIDIA module: {path}")
        vermagic = subprocess.check_output(["modinfo", "-F", "vermagic", str(path)], text=True).strip()
        version = subprocess.check_output(["modinfo", "-F", "version", str(path)], text=True).strip()
        if not vermagic.startswith(RELEASE + " ") or version != VERSION:
            raise ValueError(f"Unexpected kernel/driver version in {name}.ko: {vermagic}, {version}")
        with path.open("rb") as stream:
            sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        records.append({"name": name, "version": version, "vermagic": vermagic,
                        "size": path.stat().st_size, "sha256": sha256})
    unlock = ROOT / f"build/modules/{RELEASE}/gpu_unlock.ko"
    unlock_vermagic = subprocess.check_output(["modinfo", "-F", "vermagic", str(unlock)], text=True).strip()
    if not unlock_vermagic.startswith(RELEASE + " "):
        raise ValueError("gpu_unlock module uses a different kernel")
    result = {"schema": 1, "kernel_release": RELEASE, "nvidia_driver_version": VERSION,
              "gpu_unlock_vermagic": unlock_vermagic, "modules": records,
              "hardware_verified": False}
    (MODULES / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"Verified {len(records)} NVIDIA modules and matching gpu_unlock kernel release")
    print("This does not establish driver binding or GPU unlock on physical hardware.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"NVIDIA module verification failed: {error}")
