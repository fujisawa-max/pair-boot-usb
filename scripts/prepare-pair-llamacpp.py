#!/usr/bin/env python3
"""Verify and safely unpack the PAIR-managed llama.cpp release; never execute it."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "inputs/pair-llamacpp-inputs.json"
EXPECTED = {
    "server": ("llama-b11146-bin-ubuntu-cuda-12.8-x64.tar.gz", "llama-b11146",
               "c2ab9e19838513ff69d1af8d999ad717dd3c7ee4714ac04c7ed5ab9077c50e4e"),
    "cudart": ("cudart-llama-b11146-bin-ubuntu-cuda-12.8-x64.tar.gz",
               "cudart-llama-b11146-bin-ubuntu-cuda-12.8-x64",
               "1466daea60aad1144819e151b2bae19d54556cf1da6c129c4f55a5ded2637c25"),
}


def inspect_archive(path, top):
    files = []
    links = []
    with tarfile.open(path, "r:gz") as source:
        members = source.getmembers()
        if len(members) > 256:
            raise ValueError("Too many llama.cpp archive entries")
        seen = set()
        for member in members:
            parts = PurePosixPath(member.name).parts
            if not parts or parts[0] != top or any(part in (".", "..") for part in parts):
                raise ValueError(f"Unsafe archive path: {member.name}")
            if len(parts) == 1:
                if not member.isdir():
                    raise ValueError("Archive root is not a directory")
                continue
            if len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9_.+-]+", parts[1]):
                raise ValueError(f"Unexpected nested archive path: {member.name}")
            name = parts[1]
            if name in seen:
                raise ValueError(f"Duplicate archive entry: {name}")
            seen.add(name)
            if member.isfile():
                if member.size > 1024 * 1024 * 1024:
                    raise ValueError(f"Archive entry too large: {name}")
                files.append((name, member))
            elif member.issym():
                target = PurePosixPath(member.linkname)
                if len(target.parts) != 1 or not re.fullmatch(r"[A-Za-z0-9_.+-]+", target.name):
                    raise ValueError(f"Unsafe archive symlink: {name}")
                links.append((name, target.name))
            else:
                raise ValueError(f"Special or unsupported archive entry: {name}")
        if sum(member.size for _, member in files) > 2 * 1024 * 1024 * 1024:
            raise ValueError("Archive expanded size exceeds limit")
        for name, target in links:
            if target not in seen or target == name:
                raise ValueError(f"Broken or self-referencing archive symlink: {name}")
    return files, links


def verify_input(entry):
    name = entry["name"]
    if name not in EXPECTED:
        raise ValueError(f"Unknown llama.cpp archive: {name}")
    filename, top, expected_sha = EXPECTED[name]
    if (entry["url"] != f"https://github.com/ggml-org/llama.cpp/releases/download/b11146/{filename}"
            or entry["file"] != f".cache/{filename}"
            or entry["top_directory"] != top
            or entry["sha256"] != expected_sha):
        raise ValueError(f"Unexpected llama.cpp lock entry: {name}")
    path = ROOT / entry["file"]
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Archive must be a regular file: {path}")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected_sha:
        raise ValueError(f"SHA-256 mismatch: {name}")
    return path, *inspect_archive(path, top)


def output_path(raw):
    path = Path(raw).resolve()
    build = (ROOT / "build").resolve()
    if path == build or not path.is_relative_to(build) or path.exists():
        raise ValueError("output must be a fresh directory below build/")
    return path


def extract(inputs, destination):
    destination.mkdir(parents=True, exist_ok=False)
    for entry, (path, files, links) in inputs:
        with tarfile.open(path, "r:gz") as source:
            for name, member in files:
                target = destination / name
                if target.exists() or target.is_symlink():
                    raise ValueError(f"Duplicate output across archives: {name}")
                incoming = source.extractfile(member)
                if incoming is None:
                    raise ValueError(f"Missing archive data: {name}")
                with target.open("xb") as out:
                    while block := incoming.read(1024 * 1024):
                        out.write(block)
                    out.flush()
                    os.fsync(out.fileno())
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
            for name, link in links:
                target = destination / name
                if target.exists() or target.is_symlink():
                    raise ValueError(f"Duplicate output across archives: {name}")
                target.symlink_to(link)
    if not (destination / "llama-server").is_file() or not (destination / "libggml-cuda.so").is_file():
        raise ValueError("PAIR-managed llama.cpp server or CUDA backend missing")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=LOCK)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    lock = json.loads(args.lock.read_text())
    if ((lock.get("schema"), lock.get("pair_version"), lock.get("engine_version"), lock.get("platform"))
            != (1, "1.0.0", "b11146", "linux/amd64")):
        raise ValueError("Unsupported PAIR llama.cpp input lock")
    entries = lock["archives"]
    if not isinstance(entries, list) or {entry["name"] for entry in entries} != set(EXPECTED):
        raise ValueError("Expected exactly the server and cudart archives")
    inputs = [(entry, verify_input(entry)) for entry in entries]
    destination = output_path(args.output)
    extract(inputs, destination)
    report = {"pair_version": "1.0.0", "engine_version": "b11146", "archives": [
        {"name": entry["name"], "sha256": entry["sha256"], "url": entry["url"]} for entry in entries
    ]}
    (destination / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Verified and extracted PAIR-managed llama.cpp b11146: {destination}")
    print("No engine binary or installer was executed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, tarfile.TarError) as error:
        sys.exit(f"PAIR llama.cpp preparation failed: {error}")
