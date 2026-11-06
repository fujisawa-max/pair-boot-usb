#!/bin/sh
set -eu
if [ ! -f /run/gpu-root/state.json ]; then
  echo 'PAIR launcher requires the managed writable Linux root.' >&2
  exit 1
fi
cd /home/gpu
exec setpriv --reuid=gpu --regid=gpu --init-groups env HOME=/home/gpu PATH=/opt/nvpair/bin:/opt/llama.cpp/bin:/usr/local/bin:/usr/bin:/bin \
  XDG_CACHE_HOME=/var/cache/gpu-inference TMPDIR=/var/tmp/gpu-inference \
  LLAMA_CACHE=/home/gpu/.llamacpp /opt/nvpair/bin/nvpair-tui "$@"
