#!/bin/sh
if [ "${root:-}" = gpu-root ]; then
  rootok=1
  # Existing root is mounted by our hook; no generic root device probe.
  wait_for_dev /dev/disk/by-uuid/"$(getarg gpu_root.usb_uuid=)"
fi
