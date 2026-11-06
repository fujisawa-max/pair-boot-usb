#!/usr/bin/env python3
"""Verify a production module bundle using public artifacts only."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("signing", ROOT / "scripts/sign-gpu-modules.py")
signing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signing)


def verify(directory):
    directory = Path(directory).resolve()
    if not directory.is_relative_to((ROOT / "build").resolve()):
        raise ValueError("signed module bundle must be below build/")
    manifest_path = directory / "signing-manifest.json"
    cert = directory / "signing-cert.der"
    if any(path.is_symlink() or not path.is_file() for path in (manifest_path, cert)):
        raise ValueError("signing manifest and certificate must be ordinary files")
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get("schema") != 1 or manifest.get("purpose") != "production" or
            manifest.get("kernel_release") != signing.KERNEL or
            manifest.get("nvidia_driver_version") != signing.DRIVER or
            manifest.get("certificate_sha256") != signing.digest(cert)):
        raise ValueError("production signing manifest/certificate mismatch")
    fingerprint = signing.call("openssl", "x509", "-inform", "DER", "-in", str(cert),
                               "-noout", "-fingerprint", "-sha256").split("=", 1)[1]
    if manifest.get("certificate_sha256_fingerprint") != fingerprint:
        raise ValueError("production certificate fingerprint mismatch")
    records = manifest.get("modules")
    if not isinstance(records, list) or {item.get("name") for item in records} != set(signing.SOURCES) or len(records) != len(signing.SOURCES):
        raise ValueError("production bundle does not contain exactly six modules")
    for item in records:
        name = item["name"]
        signed = directory / (name + ".ko")
        source = signing.SOURCES[name]
        if signed.is_symlink() or not signed.is_file() or source.is_symlink() or not source.is_file():
            raise ValueError(f"missing ordinary module: {name}")
        if item.get("input_sha256") != signing.digest(source) or item.get("signed_sha256") != signing.digest(signed):
            raise ValueError(f"module hash mismatch: {name}")
        info = signing.metadata(signed)
        if (info["name"] != name.replace("-", "_") or info["vermagic"] != item.get("vermagic") or
                not info["vermagic"].startswith(signing.KERNEL + " ") or
                info["signer"] != item.get("signer") or info["sig_id"] != "PKCS#7" or
                info["sig_hashalgo"].lower() != "sha256" or
                (name != "gpu_unlock" and info["version"] != signing.DRIVER)):
            raise ValueError(f"module metadata mismatch: {name}")
        signing.verify_cms(signed, source, cert)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.directory)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        sys.exit(f"Signed module bundle rejected: {error}")
    print(f"Verified {len(result['modules'])} production signatures: {result['certificate_sha256_fingerprint']}")
