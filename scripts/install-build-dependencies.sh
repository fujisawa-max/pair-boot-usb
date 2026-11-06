#!/bin/sh
# Run as the owner in the Ubuntu/Debian build environment.
set -eu

if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && [ "$1" != '--print' ]; }; then
  echo "Usage: sh scripts/install-build-dependencies.sh [--print]" >&2
  exit 2
fi
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
build_python=${PYTHON:-python3}
packages=$("$build_python" "$root/scripts/check-environment.py" --apt-packages)
if [ "${1:-}" = '--print' ]; then
  printf '%s\n' "$packages"
  exit 0
fi
if ! command -v apt-get >/dev/null 2>&1; then
  echo "Error: apt-get is required; use an Ubuntu/Debian build environment." >&2
  exit 1
fi
if [ "$(id -u)" -eq 0 ]; then
  echo "Error: run as your normal user; sudo is used only for apt-get." >&2
  exit 1
fi
if ! command -v sudo >/dev/null 2>&1; then
  echo "Error: sudo is required to install build dependencies." >&2
  exit 1
fi
# No DKMS, kernel image, NVIDIA driver, bootloader or firmware installation.
sudo apt-get update
# Package names come from the checked-in Python constant; split words without globbing.
set -f
# shellcheck disable=SC2086
sudo apt-get install -y --no-install-recommends $packages
printf '%s\n' 'OS tools installed. Check the selected build Python next:' \
  'python scripts/check-environment.py (Conda) or python3 scripts/check-environment.py' \
  'Python >=3.11 is required. The OS python3 package may be older; see docs/build.md.'
