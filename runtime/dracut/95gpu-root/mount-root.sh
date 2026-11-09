#!/bin/sh
gpu_root_stop() {
  warn "$*; normal boot will not continue"
  while :; do
    if [ -s /run/gpu-root-boot-error.txt ]; then
      cat /run/gpu-root-boot-error.txt >&2
    else
      printf '\n\n======================================================================\nPAIR BOOT - STARTUP STOPPED\n\n%s\n\nA maintenance prompt follows. Automatic boot will not continue.\n======================================================================\n\n' "$*" >&2
    fi
    emergency_shell -n gpu-root "$*"
    sleep 1
  done
}
if [ "${root:-}" = gpu-root ] && ! ismounted /sysroot; then
  modprobe loop || gpu_root_stop 'loop module unavailable'
  modprobe ext4 || gpu_root_stop 'ext4 module unavailable'
  gpu_root_result=0
  /usr/bin/python3 /usr/lib/gpu-root.py boot 2>/run/gpu-root-boot-error.txt || gpu_root_result=$?
  if [ "$gpu_root_result" = 64 ]; then
    systemctl --no-block poweroff || gpu_root_stop 'Poweroff request failed'
    while :; do sleep 1; done
  elif [ "$gpu_root_result" != 0 ]; then
    gpu_root_stop 'GPU root setup failed; no fallback or overwrite beyond validated transactions'
  fi
  need_shutdown
fi
