#!/usr/bin/env python3
"""Configure signed Debian/NVIDIA APT indices entirely inside the workspace."""
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / ".cache/nvidia"
DEBIAN_KEYS = ROOT / ".cache/debian/keys"
KEY_URL = "https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/8793F200.pub"
FINGERPRINT = "02182E60104FCDC26EAE1B8597A5D4CB8793F200"


def main():
    for key in ("archive-key-13.asc", "archive-key-13-security.asc"):
        if not (DEBIAN_KEYS / key).is_file():
            raise ValueError(f"Missing Debian 13 repository key: {key}")
    for path in (BASE / "keys", BASE / "gpg", BASE / "apt/lists/partial",
                 BASE / "apt/archives/partial", BASE / "logs"):
        path.mkdir(parents=True, exist_ok=True)
    (BASE / "gpg").chmod(0o700)
    key_path = BASE / "keys/8793F200.asc"
    if key_path.is_symlink():
        raise ValueError("Refusing symlink NVIDIA key")
    if not key_path.exists():
        with urllib.request.urlopen(KEY_URL, timeout=30) as source:
            if source.url != KEY_URL:
                raise ValueError("NVIDIA key URL redirected")
            key_path.write_bytes(source.read(16 * 1024))
    inspected = subprocess.run(["gpg", "--homedir", str(BASE / "gpg"),
                                "--with-colons", "--show-keys", str(key_path)],
                               capture_output=True, text=True, check=True)
    fingerprints = [line.split(":")[9].upper() for line in inspected.stdout.splitlines()
                    if line.startswith("fpr:")]
    if FINGERPRINT not in fingerprints:
        raise ValueError("Unexpected NVIDIA Debian 13 signing-key fingerprint")
    sources = [
        f"deb [arch=amd64 signed-by={DEBIAN_KEYS / 'archive-key-13.asc'}] https://deb.debian.org/debian trixie main contrib",
        f"deb [arch=amd64 signed-by={DEBIAN_KEYS / 'archive-key-13-security.asc'}] https://security.debian.org/debian-security trixie-security main contrib",
        f"deb [arch=amd64 signed-by={key_path}] https://developer.download.nvidia.com/compute/cuda/repos/debian13/x86_64/ /",
    ]
    (BASE / "sources.list").write_text("\n".join(sources) + "\n")
    lines = [
        'Dir::Etc::main "-";',
        'Dir::Etc::parts "-";',
        f'Dir::Etc::sourcelist "{BASE / "sources.list"}";',
        'Dir::Etc::sourceparts "-";',
        f'Dir::State "{BASE / "apt"}";',
        f'Dir::State::status "{BASE / "status"}";',
        f'Dir::Cache "{BASE / "apt"}";',
        f'Dir::Cache::archives "{BASE / "apt/archives"}";',
        f'Dir::Log "{BASE / "logs"}";',
        'APT::Architecture "amd64";',
        'APT::Architectures { "amd64"; };',
        'Acquire::Languages "none";',
        'APT::Install-Recommends "false";',
        'APT::Install-Suggests "false";',
        'Debug::NoLocking "true";',
    ]
    (BASE / "apt.conf").write_text("\n".join(lines) + "\n")
    (BASE / "status").touch(exist_ok=True)
    print(f"Configured workspace APT: {BASE / 'apt.conf'}")
    print("No host APT source, keyring, or installed package was changed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"NVIDIA APT preparation failed: {error}")
