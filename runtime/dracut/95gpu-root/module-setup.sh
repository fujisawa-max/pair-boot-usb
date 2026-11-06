#!/bin/bash
check() { return 255; }
depends() { echo 'base shutdown'; }
installkernel() {
  hostonly='' instmods -c loop ext4 gpu_unlock
  hostonly='' instmods vfat fat nls_cp437 nls_ascii nls_utf8
  hostonly='' instmods nvme ahci sd_mod usb_storage uas xhci_pci ehci_pci
  # A USB keyboard must also work before the root image is available.
  hostonly='' instmods usbhid hid_generic
}
install() {
  # wait_for_dev in parse-root.sh needs the systemd initqueue service enabled.
  dracut_need_initqueue
  # systemd-sysusers may create mode-000 shadow files. Namespace root has no
  # host CAP_DAC_OVERRIDE, so allow owner-read before cpio archives these locked accounts.
  for file in "$initdir/etc/shadow" "$initdir/etc/gshadow"; do
    [ ! -f "$file" ] || chmod u+r "$file"
  done
  inst_multiple python3 ntfs-3g ntfs-3g.probe blkid findmnt mount umount losetup \
    e2fsck debugfs sync sha256sum modprobe modinfo ntfsinfo lsblk grep tail head \
    timeout tee setsid sulogin chroot dmesg /usr/lib/x86_64-linux-gnu/libffi.so.8 \
    /usr/lib/x86_64-linux-gnu/libkmod.so.2
  # Python modules (including ctypes/hashlib/json) and their ELF dependencies.
  local file
  while IFS= read -r file; do
    case "$file" in
      *.so) inst_binary "$file" ;;
      *) inst_simple "$file" ;;
    esac
  done < <(find /usr/lib/python3.13 -type f ! -path '*/__pycache__/*')
  inst_simple "$moddir/gpu-root.py" /usr/lib/gpu-root.py
  inst_simple "$moddir/fleet-registry.py" /usr/lib/fleet-registry.py
  inst_simple "$moddir/storage-target.py" /usr/lib/storage-target.py
  inst_simple "$moddir/root-maintenance.py" /usr/lib/root-maintenance.py
  inst_simple "$moddir/gpu-diagnostic.py" /usr/lib/gpu-diagnostic.py
  inst_simple "$moddir/gpu-unlock-diagnostic.py" /usr/lib/gpu-unlock-diagnostic.py
  inst_script "$moddir/diagnostic-unlock.sh" /usr/bin/gpu-diag-unlock
  inst_script "$moddir/diagnostic-nvidia.sh" /usr/bin/gpu-diag-nvidia
  inst_script "$moddir/diagnostic-finish.sh" /usr/bin/gpu-diag-finish
  inst_simple "$moddir/diagnostic-timeout.conf" /etc/systemd/system/dracut-pre-trigger.service.d/90-diagnostic.conf
  inst_hook cmdline 20 "$moddir/parse-root.sh"
  inst_hook pre-udev 01 "$moddir/early-unlock.sh"
  inst_hook pre-trigger 05 "$moddir/diagnostic.sh"
  # With systemd initrd, dracut-mount runs after sysroot.mount. Prepare the
  # loop root in pre-mount, which is ordered before sysroot.mount.
  inst_hook pre-mount 20 "$moddir/mount-root.sh"
  inst_hook shutdown 10 "$moddir/shutdown-root.sh"
}
