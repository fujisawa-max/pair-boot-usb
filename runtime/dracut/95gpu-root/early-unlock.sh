#!/bin/sh
# Runs before udev can bind NVIDIA/nouveau. Baseline differs only by targets opt-in.
if [ "${root:-}" = gpu-root ]; then
  # This selects a distinct, registered USB-only test path in gpu-root.py.
  # It never falls back from a failed production module load.
  [ "$(getarg gpu_root.no_gpu_test=)" != 1 ] || return 0
  if ! modprobe gpu_unlock; then
    # dracut die() may power off with rd.shell disabled. Keep the reason visible.
    while :; do
      warn 'gpu_unlock module failed to load; check MOK enrollment and kernel log; boot stopped'
      emergency_shell -n gpu-module 'GPU module could not load; do not continue Normal/Unlock boot'
      sleep 1
    done
  fi
fi
