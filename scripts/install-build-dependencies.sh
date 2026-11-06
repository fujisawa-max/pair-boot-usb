#!/bin/sh
# Run in the dedicated WSL Ubuntu terminal, outside the restricted agent sandbox.
set -eu

if [ "$#" -ne 0 ]; then
  echo "Usage: sh scripts/install-build-dependencies.sh" >&2
  exit 2
fi
if ! command -v apt-get >/dev/null 2>&1; then
  echo "Error: apt-get is required; use the dedicated WSL Ubuntu environment." >&2
  exit 1
fi
if [ "$(id -u)" -eq 0 ]; then
  echo "Error: run as your normal WSL user; sudo is used only for apt-get." >&2
  exit 1
fi
if ! command -v sudo >/dev/null 2>&1; then
  echo "Error: sudo is required to install build dependencies." >&2
  exit 1
fi
# No DKMS, kernel image, NVIDIA driver, bootloader or firmware installation.
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  build-essential pkg-config git ca-certificates curl \
  python3 xz-utils zstd dpkg-dev debhelper fakeroot \
  bubblewrap e2fsprogs kmod openssl gnupg gpgv
printf '%s\n' 'Build tools installed. USB target headers and isolated image tooling remain separate.'
