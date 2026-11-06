#!/usr/bin/env python3
"""Sign copies of the pinned GPU modules with an externally held private key."""

import argparse
import hashlib
import getpass
import os
import json
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
KERNEL = "6.12.113+deb13-amd64"
DRIVER = "615.71.10"
SIGN_FILE = BUILD / "debian-root/usr/lib/linux-kbuild-6.12.113+deb13/scripts/sign-file"
SOURCES = {
    "gpu_unlock": BUILD / f"modules/{KERNEL}/gpu_unlock.ko",
    **{name: BUILD / f"nvidia-modules/{DRIVER}/{KERNEL}/{name}.ko"
       for name in ("nvidia", "nvidia-modeset", "nvidia-drm", "nvidia-uvm", "nvidia-peermem")},
}


def call(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def metadata(path):
    return {field: call("modinfo", "-F", field, str(path))
            for field in ("name", "version", "vermagic", "signer", "sig_id", "sig_hashalgo")}


def verify_cms(signed_path, source_path, cert_path):
    """Verify the appended PKCS#7 against the source bytes and chosen cert."""
    marker = b"~Module signature appended~\n"
    with signed_path.open("rb") as stream:
        size = stream.seek(0, 2)
        if size < len(marker) + 12:
            raise ValueError(f"missing module signature trailer: {signed_path.name}")
        stream.seek(size - len(marker) - 12)
        trailer = stream.read(12)
        if stream.read(len(marker)) != marker or trailer[2] != 2:
            raise ValueError(f"invalid module signature trailer: {signed_path.name}")
        signature_length = struct.unpack(">I", trailer[8:])[0]
        if not 0 < signature_length <= 1024 * 1024:
            raise ValueError(f"invalid module signature length: {signed_path.name}")
        signature_start = size - len(marker) - 12 - signature_length
        if signature_start != source_path.stat().st_size:
            raise ValueError(f"module payload length changed: {signed_path.name}")
        stream.seek(signature_start)
        signature = stream.read(signature_length)
    with tempfile.TemporaryDirectory(prefix="gpu-sign-verify-") as directory:
        sig_file = Path(directory) / "signature.der"
        cert_file = Path(directory) / "certificate.pem"
        sig_file.write_bytes(signature)
        cert_file.write_text(call("openssl", "x509", "-inform", "DER", "-in", str(cert_path),
                                  "-outform", "PEM") + "\n")
        call("openssl", "cms", "-verify", "-binary", "-inform", "DER", "-in", str(sig_file),
             "-content", str(source_path), "-certfile", str(cert_file), "-nointern",
             "-noverify", "-out", "/dev/null")


def validate_key(key, cert):
    if key.is_symlink() or not key.is_file() or cert.is_symlink() or not cert.is_file():
        raise ValueError("key and DER certificate must be regular files, not links")
    if key.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("private key must be held outside this repository")
    if stat.S_IMODE(key.stat().st_mode) & 0o077:
        raise ValueError("private key must not be group/world accessible")
    call("openssl", "x509", "-inform", "DER", "-in", str(cert), "-noout")
    fingerprint = call("openssl", "x509", "-inform", "DER", "-in", str(cert),
                       "-noout", "-fingerprint", "-sha256")
    if not re.fullmatch(r"sha256 Fingerprint=([0-9A-F]{2}:){31}[0-9A-F]{2}", fingerprint, re.I):
        raise ValueError("invalid certificate SHA-256 fingerprint")
    return fingerprint.split("=", 1)[1]


def source_metadata():
    if not SIGN_FILE.is_file():
        raise ValueError("Debian sign-file tool is missing")
    results = {}
    for name, source in SOURCES.items():
        if source.is_symlink() or not source.is_file() or source.stat().st_size == 0:
            raise ValueError(f"missing regular module: {name}")
        info = metadata(source)
        if info["name"] != name.replace("-", "_") or not info["vermagic"].startswith(KERNEL + " "):
            raise ValueError(f"module name/kernel mismatch: {name}")
        if name != "gpu_unlock" and info["version"] != DRIVER:
            raise ValueError(f"NVIDIA driver version mismatch: {name}")
        if info["signer"] or info["sig_id"]:
            raise ValueError(f"input module is already signed: {name}")
        results[name] = {"path": source, "info": info, "sha256": digest(source)}
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", type=Path, required=True, help="private key outside repository")
    parser.add_argument("--cert", type=Path, required=True, help="corresponding public X.509 DER")
    parser.add_argument("--output", type=Path, required=True, help="new directory under build/")
    parser.add_argument("--purpose", choices=("lab", "production"), required=True)
    parser.add_argument("--prompt", action="store_true", help="owner terminal: prompt for encrypted key passphrase")
    args = parser.parse_args()
    if args.prompt:
        with_signing_pin(lambda: sign(args))
    else:
        sign(args)


def with_signing_pin(operation, reader=getpass.getpass):
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError('--prompt requires an interactive owner terminal')
    previous = os.environ.get('KBUILD_SIGN_PIN')
    try:
        os.environ['KBUILD_SIGN_PIN'] = reader('Signing key passphrase: ')
        return operation()
    finally:
        if previous is None:
            os.environ.pop('KBUILD_SIGN_PIN', None)
        else:
            os.environ['KBUILD_SIGN_PIN'] = previous


def sign(args):
    output = args.output.resolve()
    if output.exists() or output == BUILD.resolve() or not output.is_relative_to(BUILD.resolve()):
        raise ValueError("output must be a new directory under build/; devices are forbidden")
    fingerprint = validate_key(args.key, args.cert)
    inputs = source_metadata()
    output.mkdir(parents=True)
    shutil.copyfile(args.cert, output / "signing-cert.der")
    records = []
    for name, source in inputs.items():
        destination = output / f"{name}.ko"
        shutil.copyfile(source["path"], destination)
        call(str(SIGN_FILE), "sha256", str(args.key), str(args.cert), str(destination))
        signed = metadata(destination)
        if (not signed["signer"] or signed["sig_id"] != "PKCS#7" or
                signed["sig_hashalgo"].lower() != "sha256" or
                signed["vermagic"] != source["info"]["vermagic"] or
                signed["name"] != name.replace("-", "_") or
                destination.stat().st_size <= source["path"].stat().st_size):
            raise ValueError(f"signed module metadata failed validation: {name}")
        with source["path"].open("rb") as original, destination.open("rb") as result:
            while block := original.read(1024 * 1024):
                if result.read(len(block)) != block:
                    raise ValueError(f"module payload changed during signing: {name}")
        verify_cms(destination, source["path"], args.cert)
        records.append({"name": name, "input_sha256": source["sha256"],
                        "signed_sha256": digest(destination), "signer": signed["signer"],
                        "vermagic": signed["vermagic"], "sig_id": signed["sig_id"],
                        "sig_hashalgo": signed["sig_hashalgo"]})
    manifest = {"schema": 1, "purpose": args.purpose, "kernel_release": KERNEL,
                "nvidia_driver_version": DRIVER, "certificate_sha256_fingerprint": fingerprint,
                "certificate_sha256": digest(output / "signing-cert.der"),
                "modules": records, "certificate_enrolled": False, "hardware_verified": False}
    (output / "signing-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Signed and checked {len(records)} module copies in {output}")
    print(f"Certificate SHA-256 fingerprint: {fingerprint}")
    print("No key enrolled; no USB or host modules changed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"Module signing failed: {error}")
