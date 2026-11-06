#!/bin/sh
# Interpreter invocation works even when the Live USB is mounted noexec.
gui_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
exec /usr/bin/python3 "$gui_dir/fedora-gui.py"
