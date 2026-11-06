#!/usr/bin/env python3
"""Install launchers in the ephemeral Fedora Live session; no USB/Windows writes."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def install(home, desktop):
    home = home.resolve()
    desktop = desktop.resolve()
    if not desktop.is_relative_to(home):
        raise ValueError('Desktop must be inside the Live user home')
    source = HERE / 'gpu-linux.desktop'
    applications = home / '.local/share/applications'
    applications.mkdir(parents=True, exist_ok=True)
    desktop.mkdir(parents=True, exist_ok=True)
    targets = [applications / 'gpu-linux.desktop', desktop / 'gpu-linux.desktop']
    for path in targets:
        if path.is_symlink():
            raise ValueError('Refusing launcher symlink')
        shutil.copy2(source, path)
        path.chmod(0o755)
    if shutil.which('gio'):
        subprocess.run(['gio', 'set', str(targets[1]), 'metadata::trusted', 'true'], capture_output=True)
    return targets


def main():
    home = Path.home()
    if os.geteuid() == 0 or not home.resolve().is_relative_to(Path('/home')):
        sys.exit('Launch the setup as the Fedora Live desktop user.')
    desktop = home / 'Desktop'
    if shutil.which('xdg-user-dir'):
        value = subprocess.run(['xdg-user-dir', 'DESKTOP'], capture_output=True, text=True)
        if value.returncode == 0 and value.stdout.strip():
            desktop = Path(value.stdout.strip())
    install(home, desktop)
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '/run/user/' + str(os.getuid())))
    if runtime.resolve() != Path('/run/user/' + str(os.getuid())):
        raise ValueError('Unexpected Live runtime directory')
    marker = runtime / 'gpu-linux-gui-started'
    try:
        with marker.open('x') as file:
            file.write('started\n')
    except FileExistsError:
        return
    subprocess.Popen(['/usr/bin/python3', str(HERE / 'fedora-gui.py')], start_new_session=True)


if __name__ == '__main__':
    main()
