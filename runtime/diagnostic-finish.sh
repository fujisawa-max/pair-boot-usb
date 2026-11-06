#!/bin/sh
# All diagnostic mounts are RAM or read-only USB; never unmount Windows devices.
for path in /run/gpu-diagnostic/root/etc/machine-id /run/gpu-diagnostic/root/run \
            /run/gpu-diagnostic/root/sys /run/gpu-diagnostic/root/proc \
            /run/gpu-diagnostic/root/dev /run/gpu-diagnostic/root /run/gpu-diagnostic/usb; do
  if findmnt -n -M "$path" > /dev/null; then
    if ! umount "$path"; then
      echo "GPU DIAGNOSTIC STOP: $path is busy; leave the console open" >&2
      exit 1
    fi
  fi
done
systemctl --no-block poweroff
