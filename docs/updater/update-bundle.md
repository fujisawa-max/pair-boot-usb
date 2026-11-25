# Update manifests and managed payloads

[日本語 (Japanese)](update-bundle.ja.md)

The build can produce an update bundle for production contract **1.1**. It contains a whole-image read source, `manifest.json`, explicitly listed management files, and a verification report. It does not write a USB drive. Applying the bundle requires an updater that implements this contract and a separately trusted LOGS merge helper; those components are developed in the updater project.

## Generate during a build

Prepare and sign the modules as described in the [build instructions](../build.md). Start at sequence 1 when there is no archived previous release.

```sh
python3 scripts/build-usb.py image --update-bundle --update-sequence 1
```

The build creates the whole image in the workspace first. Once bundle verification succeeds, use `images/gpu-linux-usb.img` in the saved bundle for both flashing and updates.

- `build/usb-image-source.json`: actual Git base, public source inventory, and uncommitted changes.
- `build/usb-image-inspection-tools/`: extracted, hash-pinned Debian inspection tools.
- `build/update-bundles/<version>/standard/sequence-1/manifest.json`: update contract and file hashes.
- `build/update-bundles/<version>/standard/sequence-1/images/`: the whole image, used for flashing and as the source of updated partition ranges.
- `build/update-bundles/<version>/standard/sequence-1/payload/`: individually listed management files.
- `build/update-bundles/<version>/standard/sequence-1/reports/verification.json`: source, external-input, signature, and filesystem evidence.
- `shared-media-verification.json` in the same `reports/` directory: readback record used when accepting the original flashed image as an update source.

Bundles default to `build/update-bundles/<version>/<profile>/sequence-N/` in the project repository. When building from a separate workspace, this means the repository supplied with `--source-repository`. Use `--update-output` to choose another fresh destination.

Keep the entire directory, or move it to another disk, if it will serve as a future update source. The manifest requires the image, payload, and reports for reinspection. Pass the saved bundle directory to `--accepted-image-directory` when qualifying the original flashed image as an update source.

All output locations must be fresh and must not overlap. Use `--plan` to preview commands. The original image-only command remains available without `--update-bundle`.

On the same filesystem, the image is published without copying its contents. For a destination on another filesystem, the build copies and verifies the image. It removes the workspace image path only after verification succeeds. Inspection temporarily extracts root images and other files. Failed runs retain the workspace image and partial outputs.

Commit new source files before building, or explicitly include each untracked public source using repeated `--source-extra PATH` arguments. Modified tracked files are allowed and identified in the report. Keys, private notes, caches, vendored dependencies, and generated outputs are excluded from the public source inventory.

## Select known update sources

The bundle accepts its own inspected image as an initial source. By default, it also selects the latest earlier release for the same profile from the archive, using the highest completed sequence within that release. Standard bundles accept media that was updated to that earlier release. Loan bundles accept media flashed from the earlier image. Use a new sequence higher than that of the selected bundle. Use `--plan` to see the selected source.

An explicit `--accepted-bundle` or `--accepted-image-directory` replaces automatic selection. Use `--no-previous-source` to build without older sources. To explicitly accept initial media from an earlier build, supply its verified image directory.

```sh
python3 scripts/build-usb.py image --update-bundle --update-sequence 1 \
  --accepted-image-directory build/previous-image
```

The directory must contain the whole image and its `shared-media-verification.json`; matching partition images are reused read-only when present. The producer checks actual geometry, filesystem contents, kernel/modules, public certificate, root/state metadata, and old managed-file ownership. A label or version string alone is insufficient. Legacy support is limited to the inspected `0.9.1-shared` root revision 3, registry schema 1. Missing old artifacts cannot be replaced by synthetic fixture hashes.

For media previously updated with a known bundle, select that bundle's manifest and increase the sequence:

```sh
python3 scripts/build-usb.py image --update-bundle --update-sequence 4 \
  --accepted-bundle build/update-bundles/0.99.1/standard/sequence-3/manifest.json
```

Its image, payload, report, release fingerprint, and logical inventory are checked again. `update_sequence` must be unique and strictly increasing in the distribution series. The producer rejects release downgrades and unsupported layout, certificate, boot ABI, or state-schema changes.

The updater selects a source using its release fingerprint and the paths, sizes, and SHA-256 hashes of the old managed files. It checks the update receipt after selection. If an initial image and an updated medium have identical fingerprints and managed files, choose a single receipt policy for the distribution.

## Add the 0.9.3 Windows worker maintenance variant

An additional source can retain the 0.9.3 boot files and root while carrying only the `gui/windows-worker.ps1` fix for merging registries without selecting a USB. Keep the original 0.9.3 source and add the maintenance variant as `source-0-9-3-r2-worker-merge-fix`. Its project/root version remains 0.9.3.

Explicitly select the original image and the received `accepted-source.json`. For an existing image with its build-source record and prepared inspection tools, run the following. Use a fresh output location and a sequence greater than previously distributed values.

```sh
python3 scripts/prepare-update-bundle.py \
  --image-directory build/usb-image \
  --source-record build/usb-image-source.json \
  --source-root . --signed-modules build/signed-modules \
  --accepted-image-directory build/usb-image-0.9.3-official \
  --accepted-worker-merge-fix build/source-variant-0.9.3-r2-worker-merge-fix/accepted-source.json \
  --output build/update-bundle-worker-merge-fix --update-sequence 2
```

The same arguments work with `scripts/build-usb.py image --update-bundle`. The producer inspects the original image and verifies both worker versions against actual Git commits pinned in `inputs/updater-worker-merge-fix.json`. The received record must match that inspected source with only its ID and the pinned worker record changed. This does not permit arbitrary file/hash overrides. Missing commits or an uninspected original image stop generation.

**Use updater 1.0.7 or later with this bundle.** It distinguishes sources sharing a boot fingerprint by checking every declared old managed file's presence, size, and SHA on LOGS. Older tools stop on the ambiguous source. Contract 1.1 fixes `minimum_updater` at 1.0.0, so that field is retained; the operational requirement is recorded here and in the verification report.

If the distributed 0.99.0 image and payload are unchanged, no root or image rebuild is needed. Preserve the previous bundle, increase the sequence, and generate into a fresh output directory. Adding an accepted source does not authorize unknown user modifications.

## Generate for an existing image

An existing signed image can be inspected without rebuilding its root. Retain its build-source record, actual source tree, external input cache, signed modules, and image verification report. Prepare the inspection tools once:

```sh
python3 scripts/prepare-updater-inspection.py
```

Then select the original build inputs and a fresh bundle destination:

```sh
python3 scripts/prepare-update-bundle.py \
  --image-directory build/usb-image \
  --source-record build/usb-image-source.json \
  --source-root . --signed-modules build/signed-modules \
  --output build/update-bundle --update-sequence 1
```

The source record must describe the files actually used in that build. `scripts/record-build-source.py` records an actual Git base and source hashes. Its `--source-hashes` option imports an explicitly retained historical path-to-SHA256 snapshot after checking the actual files; `--commit` specifies the real base commit. It cannot reconstruct missing provenance from a release number.

## Preservation boundaries

EFI, MASTER, and LOGS are required update roles; Fedora is governed by the contract's selection rules. The target's fifth NTFS partition belongs to the user and is preserved. Its actual geometry is detected by the updater rather than copied from the four-partition build image.

The management payload excludes the registry, derived host files, candidates, observations, signing certificate, and user data. It lists individual owned files rather than replacing `tools/` or `gui/` wholesale. Registry migration and preservation of unknown files, dates, attributes, and settings belong to the separately validated LOGS helper. The producer does not perform that migration.

Keep bundles, certificates, PC data, and inspection logs out of Git and public releases. A SHA-256 identifies bytes; it does not authenticate the publisher. Use explicitly selected, trusted local build artifacts.

The immutable schemas and validation rules are in `inputs/updater-contract/v1/schemas/production-v1/README.md` (Japanese) in the repository. Filesystem inspection of ordinary files and contract tests do not establish Windows raw-device or hardware boot compatibility.

## Apply a partial update to an existing USB

1. Back up any data you need from the target USB to a separate location. Keep the update bundle’s directory structure intact.
2. On Windows, open the GUI for a version of `pair-boot-usb-updater` that supports the media profile and update contract. Select `images/gpu-linux-usb.img` as the source image. The updater looks for `manifest.json` in the same bundle.
3. Select the target USB and check its model, capacity, and the partitions listed for update before starting. Wait for final verification to finish, then safely eject the USB and test booting from it.

For installation, resuming an interrupted update, or restoring from a backup, see `docs/usb-update.md` shipped with your version of the updater.
