#!/bin/sh
# File-only diagnostic image: never proceed to Windows storage.
[ "$(getarg gpu_root.diagnostic=)" = 1 ] || return 0

if [ "$(getarg gpu_root.allow_ntfs_write=)" != 0 ] ||
   [ -n "$(getarg gpu_unlock.targets=)" ]; then
  die 'Diagnostic boot requires NTFS writes disabled and no unlock target'
  exit 1
fi

mkdir -p /run/gpu-diagnostic
chmod 700 /run/gpu-diagnostic
echo 'GPU-ROOT DIAGNOSTIC: collecting baseline data; Windows will not be mounted' > /dev/console
for module in xhci_pci usb_storage uas sd_mod nvme usbhid hid_generic loop ext4; do
  modprobe "$module" > /dev/console 2>&1
done
sleep 10
udevadm settle --timeout=20
python3 /usr/lib/gpu-diagnostic.py collect 2>&1 | tee /run/gpu-diagnostic/collection-console.txt > /dev/console
echo 'GPU-ROOT DIAGNOSTIC: SUMMARY SCREEN (20 seconds)' > /dev/console
cat /run/gpu-diagnostic/summary.txt > /dev/console 2>&1
sleep 20
echo 'GPU-ROOT DIAGNOSTIC: BLOCK DEVICES SCREEN (20 seconds)' > /dev/console
cat /run/gpu-diagnostic/blocks.txt > /dev/console 2>&1
sleep 20
echo 'GPU-ROOT DIAGNOSTIC: reports are in /run/gpu-diagnostic' > /dev/console
echo 'Press Enter for shell. Ctrl-D returns here; it never continues normal boot.' > /dev/console
echo 'Show summary: cat /run/gpu-diagnostic/summary.txt' > /dev/console
echo 'Optional NVIDIA baseline: gpu-diag-nvidia ; safe shutdown: gpu-diag-finish' > /dev/console
while :; do
  setsid --fork --wait --ctty sulogin -e < /dev/console > /dev/console 2>&1
  echo 'GPU diagnostic shell ended. Press Enter to reopen; gpu-diag-finish shuts down.' > /dev/console
  sleep 1
done
