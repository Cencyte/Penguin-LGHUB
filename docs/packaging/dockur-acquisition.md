# Dockur acquisition experiment

Status, updated 2026-10-26: **direct-media reconstruction verified for both required Windows DLLs**; see the final section and [machine-readable evidence](../../packaging/windows-acquisition-verification.json). The older VM experiment below remains infrastructure-only: Windows installation and guest collection were not performed. These are private acquisition experiments, not the public Penguin-LGHUB installer. See the [third-party notice and research](third-party-use.md).

**Decision, 2026-10-26:** Wine remains the runtime. Replacing Wine with an always-running Dockur/Windows system is shelved for later. The existing acquisition harness is preserved; no Windows guest has been installed. Direct installation-image extraction plus update-delta reconstruction now reproduces both exact DLLs, without a donor VM.

Dockur runs QEMU/KVM inside a Linux container. It is the proposed temporary acquisition machine; it does not replace our Wine runtime, nor does G HUB run in this VM as the intended Linux solution.

Direct ISO/WIM extraction is a different alternative: it reads an image without installing/booting Windows. Both approaches can use official media. Booting a VM does not itself add extraction permissions. The owner selected the Dockur experiment first.

## Implementation and observed checks

- [Lock file](../../packaging/dockur/lock.json): Dockur 6.06, source commit and amd64 image digest, Microsoft evaluation source, exact working DLL hashes.
- [Host controller](../../tools/packaging/dockur_acquire.py): standard-library Python, no new host package dependencies. Explicit ISO input; no arbitrary mirror selection.
- [Guest collector](../../packaging/dockur/collect.ps1): runs via Dockur's OEM hook, stages two DLLs and ten fonts, records versions/hashes/signature status and guest licensing metadata, omits product keys. Guest licensing metadata is evidence, not certification.
- [Regression checks](../../tests/packaging/test_dockur_acquire.py): URL/redirect restrictions, disk reservation, isolation settings, collection completeness, path/symlink rejection, integrity and version mismatch handling.

On 2026-10-26 UTC, the pinned image was pulled and ran successfully. QEMU reported KVM present and enabled; Compose accepted the generated configuration. The preflight VM had no Windows media, no network and no desktop connection, and was removed afterward. Ten host-side checks passed, including stopping only the owned container when the disk guard trips. No Windows installation, VM-to-host collection, font comparison or Wine compatibility result has been claimed.

For this experiment's four validation stages—(1) container/KVM/recipe preflight, (2) Windows installation, (3) guest collection/integrity, (4) isolated Wine regression—**1/4 stages (25%) is verified**. This is a stage count, not an estimate that 25% of all packaging or legal work is complete. Public acquisition clearance remains unresolved.

## Why the defaults were changed

At the pinned source revision, `src/mido.sh` includes fallback logic that can replace an evaluation edition with regular media and try third-party download mirrors after an official download failure. This wrapper instead mounts an explicit read-only `/custom.iso`. Its downloader accepts only HTTPS Microsoft domains and checks every redirect; the install container has no external network. `MIDO=N` alone would not be a sufficient assurance about all upstream fallback paths.

At the earlier check, Microsoft's evaluation link pointed to build **26300.9457** media. Our working `d2d1.dll` and `ninput.dll` are both **10.0.26100.8737**. Acquiring the latest image is therefore not exact baseline reproduction. Qualifying another build requires isolated regression tests. The direct-media recipe below resolves exact acquisition using different official media; it does not qualify a newer version.

## Storage and isolation

Use a small control workspace on a filesystem supporting private Unix permissions, such as `~/.local/state/penguin-lghub/`. Put the VM disk, media and collected files on a separate spacious volume. For this workstation, `/mnt/Captures` had about 177 GiB free; root had about 57 GiB. These are observations, not constants.

The guest receives 2 vCPUs and 4 GiB RAM; the container is capped at 2 CPUs and 6 GiB. A 64 GiB sparse qcow2 disk avoids immediate full allocation. Preparation requires 80 GiB of output capacity plus a 20 GiB free-space floor. The supervisor checks the data volume, control volume and Docker storage every five seconds and stops its own container on low space, failure, completion or the automated test deadline. This is a sampled guard, not a filesystem quota or protection against unrelated writers filling the disk between samples. Do not bypass it by running Compose directly or killing the supervisor with SIGKILL.

There is no USB/GPU passthrough, host X11 mount, host networking or Docker socket mount in the guest container. The optional VM viewer binds only `127.0.0.1:18006`, with a generated password in the private compose file. No RDP port is published. Host Docker access and KVM are still powerful capabilities, not a guarantee of isolation from a malicious container. The guest can write only its designated shared data directory among the host directories intentionally shared with Windows.

The installation network is internal to Docker. Activation requiring online access is not tested or bypassed. If that blocks installation or evaluation use, resolve it explicitly before extending the test. The current experiment does not silently enable network access or reset evaluation periods.

## Commands

From the project root, test the infrastructure first:

```sh
python tools/packaging/dockur_acquire.py preflight
python -m unittest discover -s tests/packaging -v
```

The exact image is in the lock file; a missing image must be pulled before preflight or run. No host packages are installed by these commands.

For evaluation media, first complete [Microsoft's registration](https://www.microsoft.com/en-us/evalcenter/evaluate-windows-11-enterprise) and review the applicable evaluation terms. Then the downloader can fetch the recorded official media without a manual Windows filesystem:

```sh
python tools/packaging/dockur_acquire.py download \
  --evaluation-registered \
  --output /mnt/Captures/penguin-lghub-media/windows-evaluation.iso
```

It records the final URL, size and SHA-256 beside the ISO. A computed checksum checks future integrity; it is **not** a publisher-authenticated checksum. Supply `--sha256` when a verified Microsoft checksum is available. Failed/partial downloads are retained and never silently substituted or overwritten. The URL is pinned; a changed or removed source requires deliberate review.

An existing official ISO is also usable for a suitably licensed VM. The tool does not assume that having an ISO proves license ownership. Prepare a new workspace using the selected ISO's exact SHA-256:

```sh
python tools/packaging/dockur_acquire.py prepare \
  --iso /mnt/Captures/penguin-lghub-media/windows-evaluation.iso \
  --sha256 ACTUAL_SHA256_FROM_VERIFIED_MEDIA \
  --workspace "$HOME/.local/state/penguin-lghub/dockur-test-01" \
  --data-dir /mnt/Captures/penguin-lghub-dockur-test-01 \
  --license-basis registered-evaluation
```

Use `separately-licensed-windows` for an appropriately licensed non-evaluation guest. Preparation starts nothing and accepts no Windows agreement. Starting Dockur automates Windows Setup's license acceptance; the explicit run option records the operator's instruction to proceed after reviewing the selected media's terms:

```sh
python tools/packaging/dockur_acquire.py run \
  --workspace "$HOME/.local/state/penguin-lghub/dockur-test-01" \
  --accept-windows-license
```

Keep this supervisor running in a persistent terminal/session. Its default automated deadline is four hours; it is not a deadline for human interaction. Ctrl-C stops the owned container through cleanup. Logs, disks and evidence remain for diagnosis. No command deletes a VM disk or changes a working Wine prefix.

`comparison.json` in the control directory records integrity and exact baseline matches. A match is not a legal grant; a mismatch is not automatic incompatibility, but requires qualification. Rerun comparison without starting Windows:

```sh
python tools/packaging/dockur_acquire.py collect \
  --workspace "$HOME/.local/state/penguin-lghub/dockur-test-01"
```

## Remaining work for the older VM experiment

Choose/license/register the test media; complete the real guest installation and collector test; preserve the actual guest terms; compare and qualify DLL/font versions in an isolated prefix; resolve the component-use permission question. Separately, clean extraction of the 30 selected Logitech depots is now verified; registration and the complete private Wine runner still need reproduction on Manjaro followed by CachyOS. This VM experiment does not close those gates.

## Direct-media reconstruction: verified 2/2 exact matches

The completed experiment reproduces the two Windows-sourced dependency DLLs from Microsoft downloads. It uses an **ESD installation image**, rather than an ISO wrapper around an installation image. No Windows guest is installed or booted. The stock Wine 10.20 executable runs the acquisition helper in a new, headless prefix; the G HUB runtime and its working prefix remain untouched.

| DLL | Version / architecture | Reconstructed SHA-256 | Working baseline |
|---|---|---|---|
| `d2d1.dll` | `10.0.26100.8737` / AMD64 | `d1425b33d6816a03769f2d635bfee7abb6392c267abbca4f5bd27593841a39fc` | Exact match |
| `ninput.dll` | `10.0.26100.8737` / AMD64 | `0da856260c5bf440038bd7df9e0b002c55be5e540294635b87f44da0fecab90c` | Exact match |

The files also match the hashes in [Microsoft's KB5095093 file list](https://download.microsoft.com/download/d31b5e29-b433-470a-9161-5328e28dd5f5/5095093.csv). The [KB article](https://support.microsoft.com/en-gb/servicing/os/windows-11/2026/06/june-23-2026-kb5095093-os-builds-26200-8737-and-26100-8737-preview) identifies build 26100.8737. The verification measures **2/2 (100%) of the scoped DLL identities**, not completion of all packaging, licensing or application testing.

### Reproduction chain

1. Fetch the pinned Windows 11 24H2 consumer ESD from Microsoft's delivery CDN. Its Media Creation Tool catalog publishes a SHA-1; we checked that and pinned the resulting SHA-256. Select image 9, Windows 11 Pro, build 26100.4349.
2. Fetch the complete x64 KB5095093 MSU from Microsoft Update Catalog and check its publisher-provided SHA-256. The two downloads total **9,833,572,714 bytes (9.16 GiB)**.
3. Extract the two base files, their WinSxS reverse deltas, and `cabinet.dll`. The file versions inside this image are 26100.3624 (`d2d1`) and 26100.4202 (`ninput`); the image version is not the version of every constituent file.
4. Read the pinned update resources from the verified MSU. Check the CIX manifest and the exact AMD64 forward-delta paths/ranges/hashes. Extract Microsoft's `UpdateCompression.dll` from `DesktopDeployment.cab`.
5. Apply the reverse deltas to reconstruct the **26100.1591 checkpoint**. Verify each intermediate hash. Apply the KB5095093 forward deltas to obtain **26100.8737**, then verify both final hashes, sizes, PE architectures and versions.

This is individual-file reconstruction, not full Windows servicing. It does not claim to have installed KB5095093 or satisfied all prerequisites for updating a Windows installation. Resource offsets are intentionally specific to the exact pinned MSU; a changed package fails its whole-file hash before offsets are used.

The initial manual experiment and a second, fresh-directory scripted run both produced the exact target files. The scripted run is recorded in [windows-acquisition-verification.json](../../packaging/windows-acquisition-verification.json), including media, recipe and helper-source identities. Microsoft symbol-server copies were independently compared during research; **the reconstruction command does not use them**.

### The extraction helper

Wine 10.20 lacks the XPRESS-Huffman operation used by Microsoft's update decompressor. [Our helper](../../tools/packaging/apply_windows_delta.c) uses the documented [ApplyDeltaB interface](https://learn.microsoft.com/en-us/windows/win32/devnotes/msdelta-applydeltab) and a [process-local import bridge](../../tools/packaging/xpress_acquisition_bridge.h). Microsoft's cabinet library handles compression-buffer framing; the pinned open-source [ms-compress](https://github.com/coderforlife/ms-compress) decoder handles XPRESS-Huffman data. This changes two imports only inside the helper process; it does not patch the installed Wine or either reconstructed DLL.

`ms-compress` is Copyright (C) 2012 Jeffrey Bush, GPL-3.0-or-later. Its exact source archive/commit/hash are pinned and its original source notices are retained in the control directory. The new helper and bridge carry GPL-3.0-or-later identifiers; the [full GPLv3 text](../../packaging/windows-acquisition-GPL-3.0.txt) is retained here. No decoder binary is vendored. Any later distribution of compiled tools must include the applicable notices and corresponding source; this does not license Microsoft's separately acquired libraries.

The existing pinned Dockur image is used **only as a Linux extraction-tool container**, with its VM entrypoint overridden. It has no network, runs as the user's UID, has a read-only container root, and is limited to two CPUs and 2 GiB RAM. The helper's fresh Wine prefix avoids runtime configuration interference; it is **not a security sandbox**. Only hash-verified acquisition libraries are loaded. No live desktop is connected.

### Repeatable commands

Prerequisites: Python 3.12+, an existing AMD64 Wine installation (tested: stock 10.20), MinGW-w64 C/C++ compilers, passwordless Docker access through `sudo -n`, and the already-pulled container digest in the lock file. The commands install no host packages. Consumer media availability is verified at the recorded date; removed sources require deliberate review, not fallback to an unofficial mirror or a different version.

Use a spacious private cache outside the repository. The output and control directories must be new; their parent directories must already exist. The Wine control directory needs a local filesystem with normal Unix/symlink support. The downloader supports checked resumption and rejects non-Microsoft redirects. Decoder source is fetched separately from its pinned upstream GitHub archive, with a checked hash.

```sh
python tools/packaging/windows_media_acquire.py fetch \
  --work-dir /mnt/Captures/penguin-lghub-acquisition/windows-26100.8737

python tools/packaging/windows_media_acquire.py reconstruct \
  --work-dir /mnt/Captures/penguin-lghub-acquisition/windows-26100.8737 \
  --output-dir /mnt/Captures/penguin-lghub-acquisition/windows-26100.8737/reproduction-002 \
  --control-dir "$HOME/.local/state/penguin-lghub/media-reproduction-002"

python tools/packaging/windows_media_acquire.py verify \
  --directory /mnt/Captures/penguin-lghub-acquisition/windows-26100.8737/reproduction-002/dlls
```

The successful recorded run used `reproduction-001`; do not overwrite it. Final DLLs and `verification.json` remain in the output directory. Extraction intermediates and the helper prefix remain available for inspection. Downloads reserve remaining bytes plus a 2 GiB free-space floor; reconstruction requires another 2 GiB of headroom above that floor on each workspace filesystem and checks between bounded stages. This is not a quota against unrelated disk writers. There is no automatic cleanup or copying into a runtime prefix.

Validation: **24 packaging tests passed**, including download integrity/resumption, concurrent-writer exclusion, existing-prefix rejection, resource bounds, CIX architecture/hash/range selection, unsafe archive entries, and the earlier VM controller checks. The fresh-directory reconstruction itself passed all four delta applications and both final comparisons.

### Remaining public-installation gates

The exact two-DLL acquisition gate is complete. External component-use permission remains unresolved; the result establishes identity and reproducibility, not a redistribution or Wine-use grant. Keep Microsoft media, DLLs, fonts and populated prefixes outside Git. [Clean Logitech depot extraction](../../README.md#verified-logitech-depot-extraction) is also verified: 30 depots, 1,115 exact file matches. Still pending: font acquisition/qualification, Logitech installation/registration, a self-contained pinned Wine runner, and clean Manjaro then CachyOS end-to-end reproduction. No GitHub upload is part of this step.
