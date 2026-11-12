# Getting Started with the USB Drive

[日本語 (Japanese)](quick-start.ja.md)

This guide explains how to start PAIR BOOT on a target PC using the bootable USB drive you created. For image build instructions, see [build.md](build.md).

This guide covers the basic setup using an NTFS partition on the PC's internal drive for storage. To use a USB drive for storage, see [Storing root.img and Models on a USB Drive (Japanese)](user-guide.md#usbにrootimgやmodelsを保存する場合).

### Creating and Managing Signing Keys

For use with Secure Boot, the key pair consisting of the private key for module signing and the public key (certificate) for MOK enrollment must be **created by the administrator of the target PC, or with the administrator's permission**.

The private key is critical to the PC's security. Never include it on a USB drive, in the repository, or on shared storage. **Manage it appropriately in a secure location**. Only the public certificate is placed at `GPU-LOGS/signing-cert.der` on the USB drive. For details, see [secure-boot.md (Japanese)](secure-boot.md).

---

## Overview

1. **Collect information on Windows**: Collect GPU and storage destination (NTFS) information on the target PC's Windows installation, and save the candidate configuration to the USB drive.
2. **Enroll the MOK using Fedora**: Boot the bundled Fedora Live environment from the USB drive, then request and approve MOK enrollment for Secure Boot.
3. **Verify and register the configuration using Fedora**: Review the information collected on Windows and register it on the USB drive.
4. **Start PAIR BOOT**: Boot PAIR BOOT from the USB drive and use the inference environment.

---

## Steps

Collected JSON files default to `GPU-LOGS/data/`. Files previously saved at the USB root remain selectable.

### 1. Collect GPU and Storage Information on Windows

1. Start Windows on the target PC and connect the USB drive.
2. Run `start-windows-gui.cmd` on the USB drive to open the management GUI.
3. Specify the drive and folder for storing inference models and state data (the default is `D:\GPU-Linux`), and check the GPU information.

   Note: If the PC has no partition other than C: and you want to store `root.img` and related files on a USB drive, select “Use USB as the root storage destination” (`USBをroot保存先に指定`). For detailed instructions, see [Storing root.img and Models on a USB Drive (Japanese)](user-guide.md#usbにrootimgやmodelsを保存する場合).

4. Save the candidate configuration (JSON) to `GPU-LOGS/data/` on the USB drive (the default location).
   - If the BitLocker status is shown as Unknown, follow the on-screen instructions to save a BitLocker verification JSON file with administrator privileges.

### 2. Enroll the Secure Boot MOK Using the Bundled Fedora Environment

Perform this step before the first boot with Secure Boot enabled.

1. Restart the PC, select the USB drive from the boot menu, and start the bundled “Fedora Live” environment.
2. In the management GUI, check the certificate and enrollment status. If the certificate is not enrolled, request MOK enrollment and set a temporary password for approval.
3. Restart the PC. When the blue “MokManager” screen appears, select “Enroll MOK” and enter the temporary password you just set to complete approval.
4. Boot Fedora again and confirm that the management GUI shows the MOK as enrolled (`登録済み`).

### 3. Verify the Information Collected on Windows and Register It on the USB Drive

1. Open the management GUI in Fedora Live.
2. Load the candidate JSON file exported from Windows in Step 1, and check the displayed PC identification information, storage partition, GPU model, and storage folder.

   Note: If the BitLocker status was shown as Unknown and you obtained a separate BitLocker verification JSON file with administrator privileges, use “Select another observation JSON” (`別の観測JSONを選択する`) to select that verification file.

3. If the information is correct, register the configuration to update the registry on the USB drive.

### 4. Start PAIR BOOT

1. Restart the PC and select “Normal” from the boot menu.
2. On first boot, the root and state images are prepared automatically. When preparation is complete, the startup menu appears.
3. Start PAIR from the menu.
4. Place inference models (such as GGUF files) in the `Models` folder at the registered storage destination. For model placement and operating instructions, see [user-guide.md (Japanese)](user-guide.md).
   - Try acceleration with Unlock on supported GPUs only after confirming successful startup and operation in Normal mode.

---

## Shutdown and USB Removal

- To exit PAIR, enter `q` on screen to return to the menu, then choose to restart or power off the PC.
- Before removing the boot USB drive, always confirm that `USB CAN BE REMOVED NOW` is displayed on screen.
- Keep any separate drive or USB drive containing root or Models connected until the PC has completely powered off.
