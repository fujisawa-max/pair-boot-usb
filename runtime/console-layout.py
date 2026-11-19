#!/usr/bin/env python3
"""Pin kernel output to the log VT before selecting a visible terminal."""
import errno
import fcntl
import os
import sys

VT_ACTIVATE = 0x5606
VT_WAITACTIVE = 0x5607
TIOCLINUX = 0x541C
TIOCL_SETKMSGREDIRECT = 11
TIOCL_GETKMSGREDIRECT = 17


def activate(screen, opener=os.open, ioctl=fcntl.ioctl, closer=os.close):
    terminal = {'logs': 1, 'pair': 3}.get(screen)
    if terminal is None:
        raise ValueError('Choose logs or pair')
    fd = opener('/dev/tty0', os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        # console=tty1 selects /dev/console, but VT printk output otherwise
        # follows the foreground VT. Pin it before exposing the PAIR screen.
        ioctl(fd, TIOCLINUX, bytes((TIOCL_SETKMSGREDIRECT, 1)))
        destination = bytearray((TIOCL_GETKMSGREDIRECT,))
        ioctl(fd, TIOCLINUX, destination, True)
        if destination[0] != 1:
            raise OSError(errno.EIO, 'Kernel messages were not pinned to tty1')
        ioctl(fd, VT_ACTIVATE, terminal)
        ioctl(fd, VT_WAITACTIVE, terminal)
    finally:
        closer(fd)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise ValueError('Usage: console-layout.py logs|pair')
        activate(sys.argv[1])
    except (OSError, ValueError) as error:
        sys.exit(f'Console routing/switch failed; use Ctrl+Alt+F1 (logs) or F3 (PAIR): {error}')
