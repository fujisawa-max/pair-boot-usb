#!/bin/sh
set -eu
if [ "$#" -lt 1 ]; then
  echo 'Usage: llama-local.sh /models/model.gguf [additional llama-server options]' >&2
  exit 2
fi
model=$(realpath -e -- "$1")
shift
case "$model" in
  /models/*.gguf) ;;
  *) echo 'Error: use an existing GGUF below read-only /models.' >&2; exit 1 ;;
esac
if ! findmnt -n -o OPTIONS --target /models | tr ',' '\n' | grep -qx ro; then
  echo 'Error: /models must be a read-only mount.' >&2
  exit 1
fi
export XDG_CACHE_HOME=/var/cache/gpu-inference
export HF_HOME=/var/cache/gpu-inference/huggingface
export HF_HUB_OFFLINE=1
export TMPDIR=/var/tmp/gpu-inference
mkdir -p "$XDG_CACHE_HOME" "$TMPDIR"
exec /opt/llama.cpp/bin/llama-server --model "$model" --host 127.0.0.1 "$@"
