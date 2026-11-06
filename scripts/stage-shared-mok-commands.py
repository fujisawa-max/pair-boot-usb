#!/usr/bin/env python3
"""Generate command-only MOK helpers referring to the same USB's GPU-LOGS cert."""
import argparse
import hashlib
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def stage(certificate, destination):
    if certificate.is_symlink() or not certificate.is_file():
        raise ValueError('Expected an ordinary public DER certificate')
    # Parse only the supplied public certificate; never search for signing keys.
    subprocess.run(['openssl', 'x509', '-inform', 'DER', '-in', str(certificate),
                    '-noout', '-subject'], check=True, capture_output=True)
    fingerprint = hashlib.sha256(certificate.read_bytes()).hexdigest()
    sha1 = hashlib.sha1(certificate.read_bytes()).hexdigest()
    preamble = '''#!/bin/bash
set -euo pipefail
# The owner mounts this USB's GPU-LOGS partition read-only, then exports MOK_CERT.
: "${MOK_CERT:?Set MOK_CERT to the absolute path of GPU-LOGS/signing-cert.der}"
[[ "$MOK_CERT" = /* && -f "$MOK_CERT" && ! -L "$MOK_CERT" ]] || {
  echo "STOP: ordinary absolute certificate path required" >&2; exit 1;
}
CERT="$MOK_CERT"
[[ "$(basename -- "$CERT")" = signing-cert.der ]] || {
  echo "STOP: expected signing-cert.der" >&2; exit 1;
}
[[ "$(sha256sum -- "$CERT" | cut -d ' ' -f 1)" = EXPECTED_SHA256 ]] || {
  echo "STOP: certificate SHA-256 mismatch" >&2; exit 1;
}
live_device="$(findmnt -n -o SOURCE --target /run/initramfs/live)"
cert_device="$(findmnt -n -o SOURCE --target "$CERT")"
[[ "$live_device" = /dev/* && "$cert_device" = /dev/* ]] || {
  echo "STOP: Live medium and certificate must be backed by block devices" >&2; exit 1;
}
live_device="$(readlink -f -- "$live_device")"
cert_device="$(readlink -f -- "$cert_device")"
live_parent="$(lsblk -dn -o PKNAME "$live_device")"
cert_parent="$(lsblk -dn -o PKNAME "$cert_device")"
[[ -n "$live_parent" && "$live_parent" = "$cert_parent" ]] || {
  echo "STOP: certificate is not on the same USB as Fedora Live" >&2; exit 1;
}
[[ "$(lsblk -dn -o TRAN "/dev/$live_parent")" = usb && \
   "$(lsblk -dn -o LABEL "$cert_device")" = GPU-LOGS && \
   "$(lsblk -dn -o FSTYPE "$cert_device")" = vfat ]] || {
  echo "STOP: expected this USB's GPU-LOGS FAT partition" >&2; exit 1;
}
[[ ",$(findmnt -n -o OPTIONS --target "$CERT")," = *,ro,* ]] || {
  echo "STOP: mount the certificate partition read-only" >&2; exit 1;
}
[[ -d /sys/firmware/efi ]] && command -v mokutil >/dev/null
'''.replace('EXPECTED_SHA256', fingerprint)
    commands = {
        'mok-0-prepare': '''openssl x509 -inform DER -in "$CERT" -noout -subject -dates -fingerprint -sha256
openssl x509 -inform DER -in "$CERT" -noout -fingerprint -sha1
mokutil --sb-state
sudo mokutil --test-key "$CERT"
''',
        'mok-1-import': '''openssl x509 -inform DER -in "$CERT" -noout -subject -fingerprint -sha256
# Do not treat test-key exit 0 as proof: some builds return it for not enrolled.
enrolled_text="$(LC_ALL=C sudo mokutil --list-enrolled)"
if [[ -n "$enrolled_text" && "$enrolled_text" != *"SHA1 Fingerprint:"* ]]; then
  echo "STOP: cannot parse enrolled MOK certificates" >&2; exit 1
fi
enrolled_fingerprints="$(printf '%s\n' "$enrolled_text" | awk -F ': ' '/^[[:space:]]*SHA1 Fingerprint:/ {gsub(/:/, "", $2); gsub(/[[:space:]]/, "", $2); print tolower($2)}')"
if printf '%s\n' "$enrolled_fingerprints" | grep -Fqx EXPECTED_SHA1; then
  echo "Certificate fingerprint is present in enrolled MOK list; no import needed."
  exit 0
fi
printf 'Confirm the displayed certificate. Type IMPORT to request enrollment: '
read -r answer
[[ "$answer" = IMPORT ]] || exit 1
sudo mokutil --import "$CERT"
sudo mokutil --list-new
''',
        'mok-2-check': '''mokutil --sb-state
sudo mokutil --test-key "$CERT"
sudo mokutil --list-enrolled
sudo mokutil --list-new
''',
    }
    destination.mkdir()
    for name, command in commands.items():
        target = destination / name
        target.write_text(preamble + command.replace('EXPECTED_SHA1', sha1))
        subprocess.run(['bash', '-n', str(target)], check=True)
    (destination / 'README-FEDORA.txt').write_text('''Command-only folder; no certificate or private key is included.
The shared public certificate is GPU-LOGS/signing-cert.der on this USB.
Do not select a partition by label alone when multiple USBs are attached.
Identify the GPU-LOGS partition on the same physical USB as Fedora Live,
then mount that explicit partition read-only using the owner's manual procedure.
Export its absolute certificate path, for example:
  export MOK_CERT=/mnt/gpu-config/signing-cert.der
Then run bash mok-0-prepare, review the certificate, and if needed run
bash mok-1-import. After owner approval in MokManager and reboot, use
bash mok-2-check (mount/export again after reboot).
Helpers never mount partitions, change Secure Boot, or enroll automatically.
The manual mok-1-import command changes MOK request variables after confirmation.
Expected SHA-256: ''' + fingerprint + '\nExpected SHA-1: ' + sha1 + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--certificate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    target = args.output.resolve()
    if target.exists() or target == ROOT / 'build' or not target.is_relative_to(ROOT / 'build'):
        parser.error('output must be a fresh directory below build/')
    stage(args.certificate, target)


if __name__ == '__main__':
    main()
