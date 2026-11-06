#!/bin/sh
# Runs in the real Fedora Live system, after policy load, before livesys.
set -eu
[ -f /etc/fedora-release ]
[ -d /run/gpu-linux-launchers ]
mkdir -p /etc/xdg/autostart /usr/share/applications
cp /run/gpu-linux-launchers/autostart.desktop /etc/xdg/autostart/gpu-linux-setup.desktop
cp /run/gpu-linux-launchers/application.desktop /usr/share/applications/gpu-linux.desktop
chmod 644 /etc/xdg/autostart/gpu-linux-setup.desktop /usr/share/applications/gpu-linux.desktop
# Label copy-up ancestors and the new files with the loaded Fedora policy.
# Do not scan account/credential files or relabel the entire root filesystem.
/sbin/restorecon -F / /etc /etc/xdg /etc/xdg/autostart /usr /usr/share /usr/share/applications \
  /etc/xdg/autostart/gpu-linux-setup.desktop /usr/share/applications/gpu-linux.desktop

# Live-only GUI elevation: permit the fixed worker and its no-op probe,
# not arbitrary sudo commands. No password, shared secret or NOPASSWD: ALL.
grep -qw 'rd.live.image' /proc/cmdline
mkdir -p /etc/sudoers.d
policy=$(mktemp /run/gpu-linux-launchers/sudoers.XXXXXX)
trap 'rm -f "$policy"' EXIT HUP INT TERM
printf '%s\n' 'liveuser ALL=(root) NOPASSWD: /usr/bin/true "", /usr/bin/python3 /run/initramfs/live/gui/fedora-worker.py' > "$policy"
/usr/sbin/visudo -cf "$policy"
install -o root -g root -m 440 "$policy" /etc/sudoers.d/90-gpu-linux-gui
/sbin/restorecon -F /etc /etc/sudoers.d /etc/sudoers.d/90-gpu-linux-gui
/usr/sbin/visudo -c
