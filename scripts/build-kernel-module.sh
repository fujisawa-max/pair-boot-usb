#!/bin/sh
# Compile only, with an extracted Debian userspace and no host device/network access.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "$#" -gt 1 ]; then
  echo "Usage: sh scripts/build-kernel-module.sh [kernel-release]" >&2
  exit 2
fi
release=${1:-6.12.113+deb13-amd64}
case "$release" in
  ''|*[!a-zA-Z0-9.+_-]*)
    echo "Error: invalid kernel release." >&2
    exit 2 ;;
esac
sysroot="$root/build/debian-root"
headers="/usr/src/linux-headers-$release"
if [ ! -f "$sysroot$headers/Makefile" ]; then
  echo "Error: target headers missing: $sysroot$headers/Makefile" >&2
  exit 1
fi
if [ ! -f "$root/vendor/gpu-unlock/kernel/gpu_unlock/Kbuild" ]; then
  echo "Error: upstream module source missing." >&2
  exit 1
fi
if ! command -v bwrap >/dev/null 2>&1; then
  echo "Error: bubblewrap is required for the isolated Debian build." >&2
  exit 1
fi
mkdir -p "$root/build/modules/$release" "$sysroot/work"
work=$(mktemp -d "$root/build/module-work.XXXXXX")
cp -a "$root/vendor/gpu-unlock/kernel/gpu_unlock/." "$work/"
# Only the copied module directory is writable inside this namespace.
bwrap --unshare-all --die-with-parent --new-session \
  --ro-bind "$sysroot" / --bind "$work" /work \
  --proc /proc --dev /dev --tmpfs /tmp \
  --clearenv --setenv PATH /usr/bin:/bin:/usr/sbin --setenv HOME /tmp \
  --setenv LC_ALL C --setenv KBUILD_BUILD_USER pair-boot-usb \
  --setenv KBUILD_BUILD_HOST isolated-debian \
  -- /usr/bin/make -C "$headers" M=/work CC=gcc-14 KCFLAGS=-Werror -j2 modules
if [ ! -s "$work/gpu_unlock.ko" ]; then
  echo "Error: build completed without a nonempty gpu_unlock.ko." >&2
  exit 1
fi
cp "$work/gpu_unlock.ko" "$root/build/modules/$release/gpu_unlock.ko"
printf 'Module: %s\nBuild directory: %s\n' \
  "$root/build/modules/$release/gpu_unlock.ko" "$work"
