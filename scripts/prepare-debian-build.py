#!/usr/bin/env python3
"""Download hash-locked Debian inputs and extract a build root; never install."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def verify(path, expected):
    if path.is_symlink():
        raise ValueError(f"Refusing symlink input: {path}")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch: {path}")


def output_path(value):
    path = Path(value).absolute()
    build = ROOT / "build"
    if path == build or '..' in path.parts or not path.is_relative_to(build):
        raise ValueError("Build root must be a new directory below this workspace's build/")
    for item in (path, *path.parents):
        if item == ROOT:
            break
        if item.is_symlink():
            raise ValueError(f"Refusing symlink output path: {item}")
    if path.exists():
        raise ValueError(f"Output already exists; choose a fresh --output directory: {path}")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "build/debian-root"))
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--lock", type=Path, default=ROOT / "inputs/debian-build-inputs.json")
    parser.add_argument("--extra-lock", type=Path, action="append", default=[],
                        help="additional hash-locked Debian packages (repeatable)")
    args = parser.parse_args()
    output = output_path(args.output)
    packages = {}
    for lock_path in [args.lock, *args.extra_lock]:
        lock = json.loads(lock_path.read_text())
        for entry in lock['packages']:
            name = entry['filename']
            if name in packages and packages[name]['sha256'] != entry['sha256']:
                raise ValueError(f'Conflicting package locks: {name}')
            packages[name] = entry
    cache = ROOT / ".cache/debian/apt/archives"
    cache.mkdir(parents=True, exist_ok=True)
    inputs = []
    for entry in packages.values():
        name = entry["filename"]
        if Path(name).name != name or not name.endswith(".deb"):
            raise ValueError(f"Invalid input filename: {name}")
        url = entry["url"]
        if not url.startswith(("https://deb.debian.org/debian/pool/",
                               "https://security.debian.org/debian-security/pool/")):
            raise ValueError(f"Unexpected Debian mirror: {url}")
        path = cache / name
        if not path.exists():
            if args.offline:
                raise ValueError(f"Missing offline input: {path}")
            temporary = path.with_suffix(".download")
            if temporary.exists() or temporary.is_symlink():
                raise ValueError(f"Stale temporary input: {temporary}")
            try:
                with urllib.request.urlopen(url, timeout=60) as source:
                    if not source.url.startswith("https://"):
                        raise ValueError("Refusing non-HTTPS redirect")
                    with temporary.open("xb") as target:
                        while block := source.read(1024 * 1024):
                            target.write(block)
                verify(temporary, entry["sha256"])
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        verify(path, entry["sha256"])
        inputs.append(path)
    # All inputs are verified before any extraction. No dpkg installation/scripts.
    output.mkdir(parents=True)
    for name, target in (("bin", "usr/bin"), ("sbin", "usr/sbin"),
                         ("lib", "usr/lib"), ("lib64", "usr/lib64")):
        (output / target).mkdir(parents=True, exist_ok=True)
        (output / name).symlink_to(target)
    for path in inputs:
        subprocess.run(["dpkg-deb", "--extract", str(path), str(output)], check=True)
    for name, target in (("sh", "dash"), ("awk", "mawk")):
        path = output / "usr/bin" / name
        if not path.exists():
            path.symlink_to(target)
    for name in ("work", "out", "tree"):
        (output / name).mkdir(exist_ok=True)
    print(f"Extracted {len(inputs)} verified Debian packages to {output}")
    print("No packages, kernels, or modules were installed into the host.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"Preparation failed: {error}")
