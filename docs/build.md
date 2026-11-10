# Build Instructions

[日本語 (Japanese)](build.ja.md)

These instructions describe how to create a bootable USB image on Linux or WSL2. The build process does not install GPU drivers, a kernel, or a bootloader on the build PC. The tools in this repository do not write to a physical USB drive, so a separate image-writing tool is required.

Start the build process by running `scripts/build-usb.py prepare`. The `prepare` argument downloads and verifies external inputs, builds the unsigned modules, and prepares working files. After you sign the six GPU driver modules, run `scripts/build-usb.py` with the `image` argument to build and validate the image. Follow the steps below, starting with environment setup.

## 1. Set Up the Environment

You need x86_64 Linux/WSL2, Python **3.11 or later (the Conda environment uses 3.11)**, HTTPS access, and at least 64 GiB of free disk space. Place the build directory on a Linux filesystem. The build uses bubblewrap to restrict access from the build process to the host environment. If this isolated environment cannot be set up with ordinary user privileges, the build stops.

Clone the repository and run all subsequent commands from its root directory. If Anaconda or Miniconda is available on the Linux side, create a dedicated environment:

```sh
conda env create -f environment.yml
conda activate pair-boot-build
python --version
```

If you have already created `pair-boot-build` with Python 3.11 or later, you do not need to recreate it. Start with `conda activate pair-boot-build`. Activate the environment again whenever you open another terminal. `environment.yml` selects the Python 3.11 series for the build; it does not pin the exact versions of all packages installed by Conda.

If you do not use Conda, run `python3 --version` to confirm that Python 3.11 or later is available, and replace `python` in the commands below with the appropriate `python3` or `python3.11`. Python 3.10 and earlier are not supported because the scripts use `hashlib.file_digest`. Use the same Python interpreter throughout, including signing and image generation.

### Install Build Tools

On Ubuntu/Debian-based systems, run the following commands. You can preview the package list first:

```sh
PYTHON=python sh scripts/install-build-dependencies.sh --print
PYTHON=python sh scripts/install-build-dependencies.sh
```

This command uses sudo to update the APT package lists and install build tools, including bubblewrap (`bwrap`), fakeroot, zstd, Git, GnuPG, OpenSSL, e2fsprogs, and kmod. Even if Ubuntu 22.04 still uses Python 3.10 as its system Python, you can build with Python 3.11 from an activated Conda environment.

Tools executed on the Linux/WSL build host use packages provided by Ubuntu/Debian through APT. For the USB kernel, its headers, the build compiler, image manipulation tools, and related components, `prepare` downloads specific versions of Debian packages and extracts them into dedicated directories under `build/`. It does not install GPU drivers, DKMS, a kernel, or a bootloader on the build PC.

### Check the Environment and Run Source Tests

```sh
python scripts/check-environment.py
python scripts/run-tests.py
```

The environment check (`check-environment.py`) checks the Python version, OS and CPU architecture, required commands, and free space on the filesystem containing the repository. If a requirement is not met, it displays the reason and exits with code 1. For missing commands, it also lists the corresponding APT packages. Use `--json` to obtain machine-readable results. `scripts/build-usb.py` checks the same requirements when it runs.

`requirements.txt` contains only comments explaining that no additional pip packages need to be installed. You do not need to run `pip install -r requirements.txt`. The build script automatically downloads the Python library pycdlib to extract the required files from the Fedora ISO. It verifies the specified version and SHA-256 and loads the library from the downloaded file, so no manual installation is required. Dependencies for PAIR and other software inside the USB image are also obtained separately from the host's pip environment, using specified versions.

References: [Managing Conda environments](https://docs.conda.io/projects/conda/en/stable/user-guide/tasks/manage-environments.html), [Python file_digest documentation](https://docs.python.org/3.11/library/hashlib.html#hashlib.file_digest).

## 2. Download External Inputs and Build the Unsigned Modules

Starting with an empty `build/` directory, run:

```sh
python scripts/build-usb.py prepare
```

Validation logs are saved as regular files such as `build/prepare-01.log`.

To preview only the commands that would be executed:

```sh
python scripts/build-usb.py prepare --plan
```

The script performs the following steps in order:

1. Download packages, archives, the Fedora ISO, official public keys, and extraction tools according to the source URLs, versions, SHA-256 hashes, and other verification information in `inputs/`, and store them in `.cache/`.
2. Verify signed Debian/NVIDIA APT metadata using public keys whose fingerprints have been checked, then verify the pinned package versions, SHA hashes, published paths, and other details.
3. Fetch the specified gpu-unlock commit into `vendor/gpu-unlock`, and check the commit, working tree, and Git objects.
4. Extract the Debian build sysroot and runtime base separately, then build and validate six unsigned modules for the target kernel in an isolated environment.
5. Extract NVIDIA, PAIR, llama.cpp, networking components, gzip/public CA certificates, and signed shim/GRUB.
6. Generate firmware using the pinned upstream generator, and verify the output hashes and attribution notices.
7. Verify Fedora's official CHECKSUM signature and the ISO's SHA hash, then extract the Live tree using the pinned pycdlib wheel.
8. Assemble an intermediate runtime tree in `build/runtime-root` for use with an empty PC registration state.

The kernel and the headers required to build the GPU drivers are downloaded at specified versions. Installation commands included with the downloaded sources, such as `make install`, are not executed.

Previously downloaded files can be reused if they pass verification. If corruption is detected, the process stops rather than overwriting the files automatically. It also stops if a download fails or a specified version is no longer available from its source. Files left by interrupted downloads are downloaded again from the beginning on the next run.

Files and directories created during a build are neither deleted automatically nor reused in subsequent builds. If `prepare` stops because an output destination already exists, retain the logs for investigation, clone the repository into a new directory, and restart the build there.

### Verification Scope

For Debian/NVIDIA packages, verification follows the chain from officially signed APT metadata to the package SHA hashes. For Fedora, it verifies the official key, CHECKSUM signature, and ISO. The key sources and fingerprints are recorded in `inputs/public-keys.json`.

The gpu-unlock source is checked against the specified commit. PAIR/llama.cpp, external inputs for firmware generation, and the extraction wheel are checked against specified SHA hashes and HTTPS sources. This does not mean that the authors' digital signatures are verified for all of these inputs. APT metadata is refreshed over the network so that its expiration can be checked. If a specified version disappears from the signed index, the build does not automatically switch to another version.

References: [Debian signing keys](https://ftp-master.debian.org/keys.html), [Fedora verification instructions and signing keys](https://fedoraproject.org/security/).

## 3. Sign with Your Own Key

Follow [Creating a Signing Key and Signing Modules (Japanese)](secure-boot.md) on your own terminal.

- For the first build, create an encrypted private key and a public DER certificate outside both this repository and the USB drive.
- For updates using an existing key, sign with that key without recreating it.
- The default destination for the signed modules is `build/signed-modules`. The unsigned modules are retained.

You do not need to give an agent your private key or passphrase.

## 4. Generate and Validate the USB Image

After signing is complete, run:

```sh
python scripts/build-usb.py image
```

To use a signed module bundle with a different name:

```sh
python scripts/build-usb.py image --signed-modules build/signed-modules-update
```

The script checks consistency between the signed bundle and the original modules, verifies that the payloads are unchanged, and validates the CMS signatures using the selected certificate. It then assembles the GUI, an empty shared registry, MOK management tools, a 6 GiB `root.img`, a 1 GiB `state.img` for the initial state, initramfs, and signed EFI components. The signed bundle, GUI, and MOK tools use the same public certificate.

The final outputs are `build/usb-image/gpu-linux-usb.img`, `SHA256SUMS`, and validation reports. Validation covers GPT, FAT/ext4 filesystems, initramfs, the state template's SHA hash and initial contents, image contents, and partition readback. Logs are saved as files such as `build/image-01.log`.

For now, user-facing Markdown guides bundled on the USB use Japanese versions. The copies omit `.ja` from filenames and remove language-switch links, and links between guides are rewritten to the bundled filenames. Existing GUI references such as `QUICK-START.md` and `DETAILS.md` are retained. Linked guides are also copied into `GPU-LOGS/tools/` and Fedora's `tools/` directory. Repository originals and third-party license notices are left unchanged.

In the current development tree, `config/usb-layout.json` sets GPU-MASTER to 4608 MiB (4.5 GiB), producing an image of approximately 8.4 GB. In the previous layout, used through 0.9.1 revision 2, GPU-MASTER was 8 GiB and the entire image was approximately 12.1 GB. When writing to a physical USB drive, choose a drive whose actual capacity is at least the size of the image you are using. `scripts/build-usb.py` does not write to physical devices. Once the final image is ready, proceed to [Quick Start](quick-start.md).

GPU-MASTER is the distribution source partition. The logical sizes of `root.img` and `state.img` remain 6 GiB and 1 GiB, respectively, and both are stored as sparse files to avoid allocating storage for unused regions. Before building, the script estimates the storage required for the data. On filesystems that cannot report the locations of unused regions, it uses the full logical sizes for this calculation. The build stops if capacity is insufficient. After creation, it checks the filesystem and confirms that at least 512 MiB is free, excluding reserved blocks. Results are saved to `master-capacity.json` in the output directory.

If you want to use the remaining space for manually distributing models, create an additional NTFS partition on the USB drive and copy the models there yourself. The build tools do not create this additional partition automatically. The storage location for models being distributed is treated separately from the registered root, state, and Models storage locations used at boot.

To reassemble the image while preserving existing outputs, specify new names for all output directories:

```sh
python scripts/build-usb.py image \
  --appliance-output build/appliance-next \
  --tools-output build/shared-usb-tools-next \
  --fedora-output build/fedora-live-tools-next \
  --output build/usb-image-next
```

These outputs must be new, non-overlapping directories under `build/`.

## Source Tests and Hardware Tests

```sh
python scripts/run-tests.py
```

The tests check behavior using data and files created for testing, and also check shell script syntax. Skipped tests are distinguished from successful tests and treated as areas left unverified because of missing environment requirements. Successful builds and signature checks alone do not verify MOK enrollment, Secure Boot startup, GPU Normal/Unlock operation, PAIR participation, or clean shutdown on the target PC.

To also test the PowerShell code for the Windows tools on Linux/WSL, prepare the optional development test runner once:

```sh
python scripts/prepare-powershell-tests.py
python scripts/run-tests.py
```

This downloads PowerShell 7.5.4 over HTTPS as specified in `inputs/powershell-test-input.json`, verifies its size and SHA hash, and extracts it into `build/powershell-7.5.4`. If the output already exists, preparation stops and preserves it, so run only the tests if it has already been prepared. This runner is for x86_64 Linux/WSL. It is not installed on the host or included on the USB drive. The tests automatically detect its executable and replace Windows-specific storage operations and similar functionality with test substitutes.

Matching the overall SHA hash of another user's completed USB image is not a pass criterion, because private keys, generation timestamps, filesystem UUIDs, and other details differ. Verification instead covers external input versions and hashes, processing, signatures, internal contents, and behavior. A fresh installation does not depend on a previous root image. When recovery is needed, the design runs fsck on a copy while preserving the original.

Do not add `.cache/`, `vendor/`, `build/`, keys, certificates, or PC registration information to public Git repositories.

## Post-Build Boot Checks

To check the boot screen and root/state separation on a PC without an NVIDIA GPU, use [Boot Testing Without a GPU (Japanese)](no-gpu-test.md). That document also provides a procedure for updating only the boot components while retaining the root template from the same version. This mode is not run automatically in place of normal startup.

The system consists of a root image containing Linux itself and a state image containing settings and other information that must persist across root version changes. On first boot, the root and state templates are copied, and the settings for that PC are initialized. You can use an existing managed signing key, but consistency between the target kernel/modules and the public certificate is checked as usual.

Check the boot banner, Normal/Unlock display, PAIR startup/shutdown/restart, settings persistence after a PC restart, and clean shutdown, in that order. If processes remain after an abnormal termination, the next PAIR startup is blocked.

Root replacement is limited to updates using the same state schema from version 0.9 onward. The current 0.9.1 build uses root revision 3, with an independent revision counter; existing 0.9.0 roots are treated as revision 0. Revision 3 fixes an issue where shutdown attempted to detach an already detached state loop device again, preventing power-off. See the [User Guide (Japanese)](user-guide.md) for details.

## Configuration Metadata and Root Revisions

`config/release.json` defines the project version, root release, root revision, boot ABI, and state schema. Increment the revision whenever you change the root. Boot is blocked if the distribution source has a different SHA-256 for the same revision. Regenerating a completed root also changes its SHA because of UUIDs and other details, so increment the revision whenever you rebuild the root to distinguish it from existing distributions.

A component inventory summarizing the versions of external inputs is placed at `GPU-LOGS/MEDIA-INFO.txt` and in the root directory of the Fedora partition. It lists the Debian, kernel, gpu-unlock commit, NVIDIA, PAIR, llama.cpp, and Fedora versions. This describes the media and is distinct from information about the currently running root. The banner reads the version and revision from metadata in the same format inside the root, and displays Normal/Unlock/TEST based on the boot state.

The boot ABI defines the contract between initramfs, root, and state. Version checks cover not only the kernel but also the GPU drivers and gpu-unlock. When development changes compatibility for startup, shutdown, or settings persistence, review the ABI and state schema and ensure that incompatible combinations are rejected.

Verified areas and remaining checks are summarized in [TODO (Japanese)](../TODO.md).
