#!/bin/sh
# PAIR's engine path is also stored in existing state images.
set -eu

preset=/models/llama-presets.ini
router=0
explicit_preset=${LLAMA_ARG_MODELS_PRESET:-}
previous=
for argument do
    case "$previous:$argument" in
        --models-dir:/models|*:--models-dir=/models) router=1 ;;
        *:--models-preset|*:--models-preset=*) explicit_preset=1 ;;
    esac
    previous=$argument
done

if [ "$router" -eq 1 ] && [ -z "$explicit_preset" ] &&
    [ -f "$preset" ] && [ ! -L "$preset" ] && [ -r "$preset" ]; then
    set -- "$@" --models-preset "$preset"
fi
exec /opt/llama.cpp/bin/llama-server.real "$@"
