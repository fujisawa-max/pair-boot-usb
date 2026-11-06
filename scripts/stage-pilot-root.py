#!/usr/bin/env python3
"""Stage pinned driver, PAIR, and llama.cpp assets in a fresh Debian root tree."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
KERNEL = "6.12.113+deb13-amd64"
DRIVER = "615.71.10"
BASE = ROOT / "build/appliance-root"
RUNTIME = ROOT / f"build/nvidia-runtime-{DRIVER}"
MODULES = ROOT / f"build/nvidia-modules/{DRIVER}/{KERNEL}"
PAIR = ROOT / "build/pair-v1.0.0-headless"
ENGINE = ROOT / "build/pair-llamacpp-b11146"
DEBIAN_LOCK = ROOT / "inputs/debian-runtime-inputs.json"


def output_path(raw):
    path = Path(raw).resolve()
    build = (ROOT / "build").resolve()
    if path == build or not path.is_relative_to(build) or path.exists():
        raise ValueError("output must be a fresh directory below build/")
    return path


def inspect_inputs():
    if not (BASE / "usr/lib/os-release").is_file():
        raise ValueError("Base Debian appliance root is missing")
    if json.loads((RUNTIME / "gpu-root-nvidia-packages.json").read_text())["driver_version"] != "615.71.10-1":
        raise ValueError("NVIDIA runtime overlay has wrong version")
    if json.loads((PAIR / "verification.json").read_text())["release"] != "1.0.0":
        raise ValueError("PAIR asset verification is missing or wrong")
    if json.loads((ENGINE / "verification.json").read_text())["engine_version"] != "b11146":
        raise ValueError("llama.cpp asset verification is missing or wrong")
    for binary in (PAIR / "nvpair-tui", PAIR / "nvpair-ui-broker", ENGINE / "llama-server",
                   ENGINE / "libggml-cuda.so", ENGINE / "libcudart.so.12",
                   ENGINE / "libcublas.so.12", ENGINE / "libcublasLt.so.12",
                   RUNTIME / "usr/bin/nvidia-smi",
                   RUNTIME / "usr/lib/x86_64-linux-gnu/libcuda.so.1"):
        if not binary.is_file():
            raise ValueError(f"Required asset is missing: {binary}")
    names = ("nvidia", "nvidia-modeset", "nvidia-drm", "nvidia-uvm", "nvidia-peermem")
    for name in names:
        if not (MODULES / f"{name}.ko").is_file():
            raise ValueError(f"Compiled NVIDIA module is missing: {name}.ko")
    return names


def libc_bin_input():
    entries = json.loads(DEBIAN_LOCK.read_text())["packages"]
    package = next((item for item in entries if item["package"] == "libc-bin"), None)
    if package is None or package["version"] != "2.41-12+deb13u4":
        raise ValueError("Pinned Debian 13 libc-bin input is missing")
    path = ROOT / ".cache/debian/apt/archives" / package["filename"]
    if path.is_symlink() or not path.is_file():
        raise ValueError("Pinned Debian 13 libc-bin package is missing or a symlink")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != package["sha256"]:
        raise ValueError("Pinned Debian 13 libc-bin SHA-256 mismatch")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = output_path(args.output)
    names = inspect_inputs()
    libc_bin = libc_bin_input()
    shutil.copytree(BASE, output, symlinks=True)
    # The existing base tree predates this Essential package despite its lock.
    # dracut needs ldd/ldconfig while resolving its initramfs dependencies.
    subprocess.run(["dpkg-deb", "--extract", str(libc_bin), str(output)], check=True)
    if not (output / "usr/bin/ldd").is_file() or not (output / "usr/sbin/ldconfig").is_file():
        raise ValueError("libc-bin extraction did not provide ldd and ldconfig")
    for section in ("etc", "lib", "usr", "var"):
        source = RUNTIME / section
        if source.exists():
            shutil.copytree(source, output / section, symlinks=True, dirs_exist_ok=True)
    shutil.copytree(PAIR, output / "opt/nvpair/bin", symlinks=True)
    shutil.copytree(ENGINE, output / "opt/llama.cpp/bin", symlinks=True)
    destination = output / f"usr/lib/modules/{KERNEL}/updates/dkms"
    destination.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copy2(MODULES / f"{name}.ko", destination / f"{name}.ko")
    info = {"schema": 1, "kernel_release": KERNEL, "nvidia_driver_version": DRIVER,
            "nvidia_module_flavor": "open", "pair_version": "1.0.0",
            "llamacpp_version": "b11146", "llamacpp_cuda": "12.8",
            "status": "staged-for-pilot-not-hardware-qualified",
            "pair_engine_registration": "pending per-host pilot validation"}
    marker = output / "usr/lib/gpu-root/pilot-components.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(info, indent=2) + "\n")
    print(f"Staged driver, PAIR, and llama.cpp in {output}")
    print("No package script, binary, or kernel module was executed or installed into the host.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        sys.exit(f"Pilot root staging failed: {error}")
