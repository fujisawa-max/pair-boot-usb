#!/bin/sh
set -eu
if [ ! -f /run/gpu-root/state.json ]; then
  echo 'PAIR launcher requires the managed writable Linux root.' >&2
  exit 1
fi
exec /usr/bin/python3 /usr/lib/gpu-root/pair-session.py
