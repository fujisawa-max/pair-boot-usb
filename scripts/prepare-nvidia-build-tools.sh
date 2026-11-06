#!/bin/sh
# Create a fresh target sysroot including hash-locked NVIDIA build tools.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "$#" -ne 0 ]; then
  echo "Usage: sh scripts/prepare-nvidia-build-tools.sh" >&2
  exit 2
fi
exec python3 "$root/scripts/prepare-debian-build.py" --offline \
  --extra-lock "$root/inputs/debian-build-tools-inputs.json" \
  --output "$root/build/debian-root"
