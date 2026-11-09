#!/bin/sh
# Dracut 106 moved the previous /run to /oldsys/run before invoking hooks.
gpu_state=/oldsys/run/gpu-root
[ -f "$gpu_state/state.json" ] || gpu_state=/run/gpu-root
gpu_shutdown_fail() {
  warn "GPU-ROOT: $*; no force/lazy unmount or automatic repair"
  if [ "${final:-}" = final ]; then
    warn 'GPU-ROOT: storage cleanup incomplete; refusing automatic poweroff; manual intervention required'
    while :; do sleep 60; done
  fi
  return 1
}
if [ -f "$gpu_state/state.json" ]; then
  if ismounted /oldroot/models; then
    umount /oldroot/models || { gpu_shutdown_fail 'models busy'; return 1; }
  fi
  for gpu_bind in var/cache/gpu-inference etc/hostname etc/machine-id var/lib/gpu-root home/gpu persist; do
    if ismounted "/oldroot/$gpu_bind"; then
      umount "/oldroot/$gpu_bind" || { gpu_shutdown_fail "state mount $gpu_bind busy"; return 1; }
    fi
  done
  gpu_state_loop=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("state_loop", ""))' "$gpu_state/state.json") || return 1
  case "$gpu_state_loop" in
    '') ;;
    /dev/loop[0-9]*)
      python3 /usr/lib/detach-loop.py "$gpu_state_loop" || { gpu_shutdown_fail 'state loop cleanup not confirmed'; return 1; } ;;
    *) gpu_shutdown_fail 'invalid state loop'; return 1 ;;
  esac
  sync
  if ismounted /oldroot; then
    umount /oldroot || { gpu_shutdown_fail 'root still busy; keeping loop and NTFS'; return 1; }
  fi
  gpu_loop=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["loop"])' "$gpu_state/state.json") || return 1
  case "$gpu_loop" in /dev/loop[0-9]*) ;; *) gpu_shutdown_fail 'invalid loop state'; return 1 ;; esac
  python3 /usr/lib/detach-loop.py "$gpu_loop" || { gpu_shutdown_fail 'root loop cleanup not confirmed'; return 1; }
  if ismounted "$gpu_state/ntfs"; then
    sync -f "$gpu_state/ntfs" || { gpu_shutdown_fail 'NTFS sync failed'; return 1; }
    umount "$gpu_state/ntfs" || { gpu_shutdown_fail 'NTFS still busy'; return 1; }
  fi
  rm "$gpu_state/state.json"
fi
unset gpu_loop gpu_state_loop gpu_state
