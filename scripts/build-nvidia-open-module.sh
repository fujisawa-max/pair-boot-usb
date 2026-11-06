#!/bin/sh
# Build pinned NVIDIA open kernel modules in the extracted Debian 13 userspace.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
release=6.12.113+deb13-amd64
version=615.71.10
package="$root/.cache/nvidia/apt/archives/nvidia-kernel-open-dkms_${version}-1_amd64.deb"
sysroot="$root/build/debian-root"
expected=542369ff53e43fa6b7415d67be68c22b70ec4cfe89d492c08886c67b1204bb1c

if [ ! -f "$package" ] || [ -L "$package" ]; then
  echo "Error: pinned NVIDIA source package is missing or a symlink." >&2
  exit 1
fi
actual=$(sha256sum "$package")
actual=${actual%% *}
if [ "$actual" != "$expected" ]; then
  echo "Error: NVIDIA source package SHA-256 mismatch." >&2
  exit 1
fi
if [ ! -f "$sysroot/usr/src/linux-headers-$release/Makefile" ]; then
  echo "Error: matching target kernel headers are missing." >&2
  exit 1
fi
if [ ! -f "$sysroot/usr/src/linux-headers-6.12.113+deb13-common/include/linux/stdarg.h" ]; then
  echo "Error: target kernel common headers are missing." >&2
  exit 1
fi
if [ ! -x "$sysroot/usr/bin/x86_64-linux-gnu-g++-14" ]; then
  echo "Error: Debian 13 G++ 14 is missing from the isolated sysroot." >&2
  exit 1
fi
if [ ! -x "$sysroot/usr/bin/xz" ]; then
  echo "Error: Debian 13 xz is missing from the isolated sysroot." >&2
  exit 1
fi
if ! command -v bwrap >/dev/null 2>&1; then
  echo "Error: bubblewrap is required for the isolated build." >&2
  exit 1
fi
work=$(mktemp -d "$root/build/nvidia-work.XXXXXX")
dpkg-deb --extract "$package" "$work"
source_dir="$work/usr/src/nvidia-$version"
if [ ! -f "$source_dir/Makefile" ]; then
  echo "Error: verified NVIDIA package has no module Makefile." >&2
  exit 1
fi
printf 'NVIDIA build work directory: %s\n' "$work"
bwrap --unshare-all --die-with-parent --new-session \
  --ro-bind "$sysroot" / --bind "$source_dir" /work \
  --proc /proc --dev /dev --tmpfs /tmp \
  --clearenv --setenv PATH /usr/bin:/bin:/usr/sbin \
  --setenv HOME /tmp --setenv LC_ALL C \
  --setenv KBUILD_BUILD_USER pair-boot-usb \
  --setenv KBUILD_BUILD_HOST isolated-debian \
  --setenv KBUILD_BUILD_TIMESTAMP '2026-09-24 00:00:00 UTC' \
  --setenv SOURCE_DATE_EPOCH 1790208000 \
  -- /usr/bin/make -C /work CC=gcc-14 CXX=x86_64-linux-gnu-g++-14 \
  NV_BUILD_USER=pair-boot-usb NV_BUILD_HOST=isolated-debian \
  KERNEL_UNAME="$release" SYSSRC=/usr/src/linux-headers-6.12.113+deb13-common \
  SYSOUT="/usr/src/linux-headers-$release" \
  IGNORE_PREEMPT_RT_PRESENCE=1 IGNORE_XEN_PRESENCE=1 -j2 modules
out="$root/build/nvidia-modules/$version/$release"
mkdir -p "$out"
for name in nvidia nvidia-modeset nvidia-drm nvidia-uvm nvidia-peermem; do
  if [ ! -s "$source_dir/kernel-open/$name.ko" ]; then
    echo "Error: build did not produce $name.ko" >&2
    exit 1
  fi
  cp "$source_dir/kernel-open/$name.ko" "$out/$name.ko"
done
(cd "$out" && sha256sum ./*.ko > SHA256SUMS)
printf 'Built five NVIDIA open modules in %s; none installed into the host.\n' "$out"
