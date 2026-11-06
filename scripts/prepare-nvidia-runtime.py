#!/usr/bin/env python3
"""Verify and extract NVIDIA driver userspace packages without installing them."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "inputs/nvidia-driver-inputs.json"
ARCHIVES = ROOT / ".cache/nvidia/apt/archives"
NVIDIA_PREFIX = "https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/"
EXCLUDE = {"dkms", "nvidia-kernel-open-dkms"}


def output_path(raw):
    path = Path(raw).resolve()
    build = (ROOT / "build").resolve()
    if path == build or not path.is_relative_to(build) or path.exists():
        raise ValueError("output must be a fresh directory below build/")
    return path


def verify(package):
    filename = package["filename"]
    if Path(filename).name != filename or not filename.endswith(".deb"):
        raise ValueError(f"Unsafe package filename: {filename}")
    path = ARCHIVES / filename
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing regular package: {path}")
    if path.stat().st_size != package["size"]:
        raise ValueError(f"Package size mismatch: {filename}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != package["sha256"]:
        raise ValueError(f"Package SHA-256 mismatch: {filename}")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    lock = json.loads(LOCK.read_text())
    if (lock.get("schema"), lock.get("kernel_release"), lock.get("nvidia_driver_version")) != (
            1, "6.12.113+deb13-amd64", "615.71.10-1"):
        raise ValueError("Unexpected NVIDIA driver input lock")
    packages = [package for package in lock["packages"]
                if package["url"].startswith(NVIDIA_PREFIX) and package["package"] not in EXCLUDE]
    if len(packages) != 24 or not {"nvidia-driver-cuda", "firmware-nvidia-gsp", "libcuda1", "libnvidia-ml1"}.issubset(
            {package["package"] for package in packages}):
        raise ValueError("Incomplete NVIDIA runtime package set")
    inputs = [(package, verify(package)) for package in packages]
    output = output_path(args.output)
    output.mkdir(parents=True)
    for package, path in inputs:
        subprocess.run(["dpkg-deb", "--extract", str(path), str(output)], check=True)
    (output / "gpu-root-nvidia-packages.json").write_text(json.dumps({
        "driver_version": "615.71.10-1",
        "packages": [{"name": package["package"], "sha256": package["sha256"]}
                     for package, _ in inputs]}, indent=2) + "\n")
    print(f"Verified and extracted {len(inputs)} NVIDIA runtime packages into {output}")
    print("No maintainer script, package installer, or kernel module was executed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f"NVIDIA runtime preparation failed: {error}")
