#!/bin/sh
gpu_root_stop() {
  warn "$*; normal boot will not continue"
  while :; do
    emergency_shell -n gpu-root "$*"
    sleep 1
  done
}
if [ "${root:-}" = gpu-root ] && ! ismounted /sysroot; then
  modprobe loop || gpu_root_stop 'loop module unavailable'
  modprobe ext4 || gpu_root_stop 'ext4 module unavailable'
  if ! /usr/bin/python3 /usr/lib/gpu-root.py boot; then
    gpu_root_stop 'GPU root setup failed; no fallback, repair or overwrite attempted'
  fi
  need_shutdown
fi
