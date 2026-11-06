#!/usr/bin/env python3
"""Record downloaded debs only after matching authenticated local APT indices."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
base = root / ".cache/debian"
environment = dict(os.environ, APT_CONFIG=str(base / "apt.conf"))
records = []
for deb in sorted((base / "apt/archives").glob("*.deb")):
    fields = subprocess.check_output(["dpkg-deb", "-f", str(deb), "Package", "Version", "Architecture"], text=True)
    metadata = dict(line.split(": ", 1) for line in fields.splitlines())
    data = subprocess.check_output(["apt-cache", "show", f"{metadata['Package']}={metadata['Version']}"],
                                   env=environment, text=True, stderr=subprocess.DEVNULL)
    values = dict(line.split(": ", 1) for line in data.split("\n\n")[0].splitlines()
                  if ": " in line and not line.startswith(" "))
    with deb.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    if sha != values["SHA256"]:
        sys.exit(f"Hash mismatch against APT index: {deb}")
    filename = values["Filename"]
    mirror = ("https://security.debian.org/debian-security/" if filename.startswith("pool/updates/")
              else "https://deb.debian.org/debian/")
    records.append({"package": metadata["Package"], "version": metadata["Version"],
                    "architecture": metadata["Architecture"], "filename": deb.name,
                    "url": mirror + filename, "sha256": sha})
print(json.dumps({"schema": 1, "kernel_release": "6.12.113+deb13-amd64",
                  "metadata_verification": "SHA256 matched local signed APT indices; see runtime-storage-design.md",
                  "packages": records}, indent=2))
