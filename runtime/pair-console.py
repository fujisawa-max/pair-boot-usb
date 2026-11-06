#!/usr/bin/env python3
"""Supervise the unprivileged PAIR TUI and explicit local power choices."""
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import termios


def usb_notice(state_path=Path('/run/gpu-root/state.json'),
               shutdown_state=Path('/run/gpu-root/shutdown-ready.json'),
               shutdown=Path('/run/initramfs/shutdown'), verify=None):
    try:
        state = json.loads(state_path.read_text())
        if json.loads(shutdown_state.read_text()).get('ready') is not True or not os.access(shutdown, os.X_OK):
            raise ValueError('RAM shutdown environment is not ready')
        if verify is None:
            verify = runpy.run_path(str(Path(__file__).with_name('announce-usb-ready.py')))['verify']
        verify(state)
        notice = 'USB CAN BE REMOVED NOW: the boot USB is no longer in use.'
        if state.get('root_storage_transport') == 'usb':
            notice += '\nKeep the separate root/Models storage USB inserted until shutdown.'
        return notice
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        return 'KEEP USB INSERTED: removal was not verified.\nReason: ' + str(error)


def start_pair():
    # This existing launcher drops UID and groups before executing PAIR.
    return subprocess.run(['/usr/local/bin/gpu-pair'], check=False).returncode


def request_power(action):
    # Do not wait for the shutdown job that will stop this very service.
    return subprocess.run(['/usr/bin/systemctl', '--no-block', action], check=False).returncode


def wait_for_shutdown():
    # Default SIGTERM terminates this supervisor; never reopen the menu here.
    while True:
        signal.pause()


def terminal_restore(source):
    if not source.isatty():
        return lambda: None
    descriptor = source.fileno()
    settings = termios.tcgetattr(descriptor)
    return lambda: termios.tcsetattr(descriptor, termios.TCSAFLUSH, settings)


def menu(notice, source, output, launch=start_pair, power=request_power, wait=wait_for_shutdown,
         restore=None):
    first = True
    while True:
        if restore is not None:
            # Recover canonical input even if the TUI crashed in raw mode.
            restore()
        if output.isatty():
            print('\033[0m\033[?25h', end='', file=output)
        print('\nGPU Linux - local console', file=output)
        print(('USB check at startup:\n' + notice), file=output)
        print('Reinsert the boot USB before booting this PC again.', file=output)
        print('\n1. ' + ('Start PAIR' if first else 'Restart PAIR'), file=output)
        print('2. Restart PC\n3. Power off PC', file=output)
        print('Choose 1-3 [Enter = 1]: ', end='', file=output, flush=True)
        line = source.readline()
        if line == '':
            print('\nConsole input closed; no power action requested.', file=output, flush=True)
            return 0
        choice = line.strip() or '1'
        if choice == '1':
            try:
                status = launch()
                first = False
                print(f'\nPAIR exited (status={status}).', file=output, flush=True)
            except OSError as error:
                print(f'\nPAIR could not start: {error}', file=output, flush=True)
        elif choice in ('2', '3'):
            action = {'2': 'reboot', '3': 'poweroff'}[choice]
            try:
                status = power(action)
            except OSError as error:
                print(f'\nPower request failed: {error}', file=output, flush=True)
                continue
            if status != 0:
                print(f'\nPower request rejected (status={status}); PC remains running.', file=output, flush=True)
                continue
            print('\nShutdown/restart requested. Wait; do not unplug root storage.', file=output, flush=True)
            wait()
            return 0
        else:
            print('\nEnter 1, 2 or 3. No action was taken.', file=output, flush=True)


def main():
    if os.geteuid() != 0 or not Path('/run/gpu-root/state.json').is_file():
        sys.exit('Managed GPU Linux root and service privileges are required.')
    # Ctrl-C may stop PAIR, but must not kill the menu supervisor. A caught
    # handler is reset to the default when the child execs the PAIR launcher.
    signal.signal(signal.SIGINT, lambda *_: None)
    return menu(usb_notice(), sys.stdin, sys.stdout, restore=terminal_restore(sys.stdin))


if __name__ == '__main__':
    sys.exit(main())
