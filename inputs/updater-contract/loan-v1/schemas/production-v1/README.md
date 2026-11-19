# Proposed loan-media update contract 1.2

This producer-authored extension is a proposal for pair-boot-usb-updater.
Existing updater versions support only the original standard-media contract 1.1
and MUST reject this contract until explicitly updated and qualified.
Do not install or execute a validator received from an untrusted update bundle.
The updater must ship and hash-pin this contract itself.

Layout: pair-loan-gpt512-v1; EFI 64 MiB, MASTER 4608 MiB, LOGS 256 MiB.
No Fedora partition. Optional user NTFS data starts at partition 4 and is preserved.
LOGS merging preserves registry, derived hosts, logs, user data and public certificates.
Only MEDIA-INFO.txt is a managed replacement file; no management executables are added.
The baseline schema vocabulary and helper protocol remain unchanged.
minimum_updater retains the schema's legacy 1.0.0 constant; it is NOT an assertion
that those updater releases implement this new contract. Use the contract lock
and supported layout capability to gate execution.
