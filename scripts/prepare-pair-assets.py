#!/usr/bin/env python3
"""Verify and extract the pinned PAIR headless release into a workspace directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "inputs/pair-headless-input.json"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify_archive(path, expected):
    if path.is_symlink() or not path.is_file():
        raise ValueError("PAIR archive must be a regular file, not a symlink")
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("Invalid pinned SHA-256")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise ValueError("PAIR archive SHA-256 mismatch")


def inspect_zip(archive, version):
    with zipfile.ZipFile(archive) as source:
        infos = source.infolist()
        if len(infos) != 14:
            raise ValueError("Unexpected PAIR archive file count")
        names = [item.filename for item in infos]
        if len(set(names)) != len(names) or "manifest.json" not in names:
            raise ValueError("Duplicate or missing PAIR archive entries")
        for item in infos:
            name = item.filename
            mode = item.external_attr >> 16
            if not re.fullmatch(r"(?:manifest\.json|[a-z0-9-]+)", name):
                raise ValueError(f"Unsafe PAIR archive path: {name}")
            if stat.S_IFMT(mode) != stat.S_IFREG:
                raise ValueError(f"PAIR archive entry is not regular: {name}")
            if item.file_size > 32 * 1024 * 1024:
                raise ValueError(f"PAIR archive entry too large: {name}")
        manifest = json.loads(source.read("manifest.json"))
        if (manifest.get("release"), manifest.get("platform"), manifest.get("arch")) != (version, "linux", "x64"):
            raise ValueError("PAIR embedded release/platform/arch differs from lock")
        listed = manifest.get("files")
        if not isinstance(listed, list) or len(listed) != 13 or {x.get("fileName") for x in listed} != set(names) - {"manifest.json"}:
            raise ValueError("PAIR embedded manifest does not list every binary")
        output = {"release": version, "platform": "linux", "arch": "x64", "files": []}
        for entry in listed:
            data = source.read(entry["fileName"])
            if len(data) != entry["size"] or digest(data) != entry["sha256"]:
                raise ValueError(f"PAIR embedded binary mismatch: {entry['fileName']}")
            if data[:4] != b"\x7fELF":
                raise ValueError(f"PAIR binary is not ELF: {entry['fileName']}")
            output["files"].append({"name": entry["fileName"], "size": len(data), "sha256": digest(data)})
        return output


def output_path(raw):
    path = Path(raw).resolve()
    if not path.is_relative_to((ROOT / "build").resolve()) or path == (ROOT / "build").resolve() or path.exists():
        raise ValueError("output must be a fresh directory below build/")
    return path


def extract(archive, destination):
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            path = destination / item.filename
            with path.open("xb") as stream:
                stream.write(source.read(item))
                stream.flush()
                os.fsync(stream.fileno())
            path.chmod(0o644 if item.filename == "manifest.json" else 0o755)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    lock = json.loads(args.lock.read_text())
    if lock.get("schema") != 1 or lock.get("version") != "1.0.0":
        raise ValueError("Unsupported PAIR input lock")
    if lock.get("url") != "https://github.com/NVIDIA/Personal-AI-Router/releases/download/v1.0.0/service-binaries-linux-x64.zip":
        raise ValueError("Unexpected PAIR release URL")
    archive = ROOT / lock["archive_file"]
    if not archive.resolve().is_relative_to((ROOT / ".cache").resolve()):
        raise ValueError("PAIR archive must be inside workspace .cache/")
    verify_archive(archive, lock["sha256"])
    report = inspect_zip(archive, lock["version"])
    destination = output_path(args.output)
    extract(archive, destination)
    (destination / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Verified and extracted PAIR {lock['version']} headless binaries: {destination}")
    print("No PAIR binary, installer, or background service was executed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        sys.exit(f"PAIR preparation failed: {error}")
