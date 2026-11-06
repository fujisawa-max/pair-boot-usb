#!/usr/bin/env python3
"""Lock the downloaded NVIDIA/Debian driver closure against signed APT indices."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / ".cache/nvidia"
APT = str(BASE / "apt.conf")
ENV = dict(os.environ, APT_CONFIG=APT)
ROOT_PACKAGES = ["nvidia-driver-cuda=615.71.10-1",
                 "nvidia-kernel-open-dkms=615.71.10-1",
                 "linux-headers-6.12.113+deb13-amd64"]
MIRRORS = {
    "nvidia": "https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/",
    "debian": "https://deb.debian.org/debian/",
    "security": "https://security.debian.org/debian-security/",
}


def first_stanza(data):
    values = {}
    for line in data.split("\n\n", 1)[0].splitlines():
        if ": " in line and not line.startswith(" "):
            name, value = line.split(": ", 1)
            values[name] = value
    return values


def main():
    simulated = subprocess.run(["apt-get", "-c", APT, "-s", "install", *ROOT_PACKAGES],
                               capture_output=True, text=True, check=True)
    expected = dict(re.findall(r"^Inst (\S+) \((\S+)", simulated.stdout, re.M))
    if not expected:
        raise ValueError("APT produced no install closure")
    records = []
    for package in sorted((BASE / "apt/archives").glob("*.deb")):
        if package.is_symlink():
            raise ValueError(f"Refusing symlink package: {package}")
        data = subprocess.check_output(["dpkg-deb", "-f", str(package),
                                        "Package", "Version", "Architecture"], text=True)
        info = first_stanza(data)
        if expected.get(info["Package"]) != info["Version"]:
            raise ValueError(f"Downloaded package not in simulated closure: {package.name}")
        metadata = subprocess.check_output(["apt-cache", "show", f"{info['Package']}={info['Version']}"],
                                           env=ENV, text=True, stderr=subprocess.DEVNULL)
        index = first_stanza(metadata)
        with package.open("rb") as stream:
            sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        if sha256 != index["SHA256"] or package.stat().st_size != int(index["Size"]):
            raise ValueError(f"APT index hash/size mismatch: {package.name}")
        filename = index["Filename"]
        if filename.startswith("./"):
            url = MIRRORS["nvidia"] + filename[2:]
        elif filename.startswith("pool/updates/"):
            url = MIRRORS["security"] + filename
        elif filename.startswith("pool/"):
            url = MIRRORS["debian"] + filename
        else:
            raise ValueError(f"Unexpected repository path: {filename}")
        records.append({"package": info["Package"], "version": info["Version"],
                        "architecture": info["Architecture"], "filename": package.name,
                        "url": url, "sha256": sha256, "size": package.stat().st_size})
    if set(expected) != {record["package"] for record in records}:
        missing = set(expected) - {record["package"] for record in records}
        raise ValueError(f"Incomplete driver closure: {sorted(missing)}")
    report = {"schema": 1, "kernel_release": "6.12.113+deb13-amd64",
              "nvidia_driver_version": "615.71.10-1", "root_packages": ROOT_PACKAGES,
              "repository_verification": "APT InRelease signatures and package SHA256 checked in workspace",
              "packages": records}
    (ROOT / "inputs/nvidia-driver-inputs.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Locked {len(records)} verified packages; {sum(x['size'] for x in records)} downloaded bytes")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f"NVIDIA input locking failed: {error}")
