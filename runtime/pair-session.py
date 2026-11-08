#!/usr/bin/env python3
"""Run one PAIR session in a transient service and clean its entire cgroup."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys

UNIT = 'gpu-pair-session.service'
DESCRIPTION = 'PAIR BOOT managed session'


def status(call):
    result = call(['/usr/bin/systemctl', 'show', UNIT, '--property=LoadState',
                   '--property=ActiveState', '--property=Description', '--property=ControlGroup'],
                  capture_output=True, text=True, timeout=10)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if values.get('LoadState') == 'not-found':
        return values
    if result.returncode or values.get('LoadState') != 'loaded' or values.get('Description') != DESCRIPTION:
        raise RuntimeError('Cannot identify the PAIR session service; refusing another session')
    return values


def empty(values, cgroups):
    group = values.get('ControlGroup', '')
    if not group:
        return values.get('LoadState') == 'not-found' or values.get('ActiveState') in ('inactive', 'failed')
    path = Path(group)
    if not path.is_absolute() or '..' in path.parts:
        raise RuntimeError('Invalid session cgroup')
    events = cgroups / group.lstrip('/') / 'cgroup.events'
    # A removed cgroup has no surviving processes, provided its unit is stopped.
    if not events.exists():
        return values.get('ActiveState') in ('inactive', 'failed')
    return 'populated 0' in events.read_text().splitlines()


def session(call=subprocess.run, cgroups=Path('/sys/fs/cgroup')):
    before = status(call)
    if before.get('LoadState') != 'not-found':
        raise RuntimeError('A previous PAIR session remains; stop and inspect it before retrying')
    command = ['/usr/bin/systemd-run', '--unit=' + UNIT, '--description=' + DESCRIPTION,
               '--service-type=exec', '--pty', '--wait', '--collect', '--quiet',
               '--property=User=gpu', '--property=Group=gpu', '--property=WorkingDirectory=/home/gpu',
               '--property=KillMode=control-group', '--property=SendSIGKILL=yes',
               '--property=TimeoutStopSec=15s', '--property=Restart=no',
               '--setenv=HOME=/home/gpu', '--setenv=XDG_CONFIG_HOME=/home/gpu/.config',
               '--setenv=XDG_CACHE_HOME=/var/cache/gpu-inference',
               '--setenv=TMPDIR=/var/tmp/gpu-inference', '--setenv=TERM=' + os.environ.get('TERM', 'linux'),
               '--setenv=PATH=/opt/nvpair/bin:/opt/llama.cpp/bin:/usr/local/bin:/usr/bin:/bin',
               '--', '/opt/nvpair/bin/nvpair-tui']
    try:
        result = call(command, check=False)
    finally:
        current = status(call)
        if current.get('LoadState') != 'not-found':
            stopped = call(['/usr/bin/systemctl', 'stop', UNIT], capture_output=True, text=True, timeout=25)
            current = status(call)
            if stopped.returncode or not empty(current, cgroups):
                raise RuntimeError('PAIR session cleanup could not be verified; restart is blocked')
    return result.returncode


def main():
    if os.geteuid() != 0 or not Path('/run/gpu-root/state.json').is_file():
        raise RuntimeError('Managed root and service privileges required')
    lock = Path('/run/gpu-root/pair-session.lock')
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return session()
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit('PAIR SESSION STOP: ' + str(error))
