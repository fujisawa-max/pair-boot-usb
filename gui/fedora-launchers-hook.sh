#!/bin/sh
# Sourced by dracut. Never copy up /sysroot/etc or /sysroot/usr here:
# before Fedora loads SELinux policy, new overlay directories may get tmpfs_t.
case "${NEWROOT:-}" in /sysroot|/sysroot/) ;; *) return 0 ;; esac
[ -f "$NEWROOT/etc/fedora-release" ] || return 0
mkdir -p /run/gpu-linux-launchers /run/systemd/system/livesys.service.wants || return 0
cp /usr/lib/gpu-linux-autostart.desktop /run/gpu-linux-launchers/autostart.desktop || return 0
cp /usr/lib/gpu-linux.desktop /run/gpu-linux-launchers/application.desktop || return 0
cp /usr/lib/gpu-linux-launchers-runtime.sh /run/gpu-linux-launchers/install.sh || return 0
cat > /run/systemd/system/gpu-linux-launchers.service <<'UNIT'
[Unit]
Description=Install GPU Linux Live launchers after SELinux policy load
Before=livesys.service display-manager.service
ConditionKernelCommandLine=rd.live.image
ConditionPathExists=/run/gpu-linux-launchers/install.sh

[Service]
Type=oneshot
ExecStart=/usr/bin/bash /run/gpu-linux-launchers/install.sh
RemainAfterExit=yes
UNIT
ln -sf ../gpu-linux-launchers.service /run/systemd/system/livesys.service.wants/gpu-linux-launchers.service
