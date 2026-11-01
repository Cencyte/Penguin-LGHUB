# Pinned Linux userspace: native libraries and G HUB backend verified

Checked October 26, 2026. Current scope: **Manjaro and CachyOS on X11**.
Bazzite remains deferred; Debian is a later host target. Wayland research now
has a [packaged-Wine keyboard transport result on both targets](2026-10-27-wayland-packaged-wine.md), while
the production launcher still requires X11. These checks
leave the working Desktop launcher and prefix unchanged.

## Measured results

The complete private Wine runs inside **Steam Linux Runtime 4**, using its
pressure-vessel tooling without Steam or a Steam account. The platform and
SDK are pinned to `4.0.20260928.262390` (glibc 2.41). Supplemental Debian
libraries are extracted into a private directory; no package manager,
maintainer script, host installation or system upgrade is involved.

| Check | Result | What this establishes |
|---|---:|---|
| Native Wine ELF dependencies | **46/46, 100%** | Every recorded module resolves its direct/transitive ELF dependencies |
| Recorded dynamic-library candidates | **54/54, 100%** | Each candidate can be loaded; this does not exercise every library function/plugin |
| Windows command in a new disposable prefix | Pass | `cmd /c echo PENGUIN_LINUX_RUNTIME_OK`, exit 0; no D-Bus assertion |
| WinRT activation factories | **8/8** | Factory acquisition, not exhaustive interface behavior |
| Registry routes | **4/4** | Application URL, tray Windows version, sensor fallback and software-manager exclusion |
| Updater service configuration | Pass | Correct command, demand start, stopped before the agent test |
| G HUB agent API requests | **5/5** | Devices list, profiles, active profile, automatic profile switching and G-Shift state queries |
| Host Wine / live desktop / physical devices | Unavailable to tests | Backend success is independent of the installed Wine and actual peripheral state |
| Fresh Manjaro/CachyOS desktop/device qualification | **0/2** | The existing workstation result does not qualify a new installation |

These denominators describe loader and backend coverage. They are not an
overall application completion percentage. An empty devices response is
expected with physical devices hidden; it does not verify device discovery,
key delivery, macro execution or G-Shift behavior on hardware.

Full sanitized evidence: [native loader report](linux-runtime-verification.json)
and [container backend report](linux-backend-verification.json).

## What changed

The first runtime audit passed 39/46 native modules and 38/54 dynamic-library
candidates. The remaining gaps are now resolved as follows:

| Component | Change |
|---|---|
| `winedmo.so` | Rebuilt from pinned upstream Wine 10.20 source against the matching SDK's FFmpeg 7.1 headers/libraries: `libavcodec.so.61`, `libavformat.so.61`, `libavutil.so.59` |
| `wpcap.so` | Rebuilt against the SDK's libpcap ABI, `libpcap.so.0.8` |
| GStreamer, gphoto2, OpenCL loader, SANE, XKB registry, ODBC and dependencies | Explicitly pinned Debian packages, privately extracted with notices intact |
| Samba `libnetapi.so.1` | Private Samba library directory added to the loader search path; ICU 76 and Jansson included in its closure |
| D-Bus assertion during the isolated test | The outer test namespace now hides `/run/dbus` as well as the session bus. A read-only socket still permits IPC. The assertion disappeared once the test could no longer contact the host system bus; no D-Bus/Wine implementation patch is claimed. |
| Container activation flags | The probe restores the recorded `WINE_GHUB_*` feature gates after sanitizing inherited Wine loader settings |
| Backend test tools | Pinned Node 22.21.0, Xvfb, keyboard compiler/keymaps and dependencies; no shell-specific nvm path or host Xvfb dependency |

No incompatible library-name aliases were introduced. No Wine modules were
removed to raise the success rate. Only the two named upstream Unix modules
are replaced in the derived runtime; all **20 pinned Wine compatibility
replacements**, protocol markers and supplementary DLLs are verified unchanged.
The baseline remains a separate, intact runtime. Two independent SDK builds
produced byte-for-byte identical module hashes. A fresh preparation and full
backend rerun also passed with spaces in both runtime and output paths; the
probe observed 128 mapped files from the selected private Wine tree.

The [runtime lock](../../packaging/linux-runtime.lock.json) contains 89
supplemental packages. The [SDK lock](../../packaging/linux-sdk.lock.json)
records the SDK archive, Wine source archive, build inputs, configure flags
and two-target allowlist. Debian package hashes were resolved from signed
Debian apt metadata in the private SDK; normal fetches enforce those recorded
SHA-256 values over official HTTPS downloads. Valve and Node hashes come from
their official checksum publications. This does not claim that each download
has an individually verified detached signature.

The [headless test-tools lock](../../packaging/linux-test-tools.lock.json)
contains Node and eight Debian packages. A read-only nested overlay exposes
only its executables and X11 keymaps at the paths Xvfb expects. It does not
replace the runtime's system libraries or write to the host. Private test-tool
libraries are selected explicitly. The original package licenses/notices
remain in the extracted trees; no downloaded binaries are committed here.

## Reproduce

Developer prerequisites: Python 3.12+, working unprivileged bubblewrap/user
namespaces and read-only overlayfs support, x86-64 Linux, an already verified
private Wine bundle and a verified freshly generated prefix candidate. The
backend probe also compiles a small static Windows test using
`x86_64-w64-mingw32-g++`; this is a development tool, not the user's system Wine.
The complete custom Wine build recipe remains separate release work; the SDK
command below rebuilds **two upstream Unix modules only**.

Use new output directories outside the checkout. These commands preserve a
2 GiB free-space floor. SDK preparation reserves 7 GiB beyond that floor;
limit build concurrency with `--jobs` (1–16, default 4).

```sh
mkdir -p "$HOME/.local/state/penguin-lghub" "$HOME/.cache/penguin-lghub/linux" \
  "$HOME/.cache/penguin-lghub/sdk" "$HOME/.cache/penguin-lghub/test-tools"

python3 tools/packaging/linux_runtime.py fetch \
  --cache "$HOME/.cache/penguin-lghub/linux"
python3 tools/packaging/linux_runtime.py prepare \
  --cache "$HOME/.cache/penguin-lghub/linux" \
  --output "$HOME/.local/state/penguin-lghub/linux-runtime"

python3 tools/packaging/linux_sdk.py fetch \
  --cache "$HOME/.cache/penguin-lghub/sdk" \
  --support-cache "$HOME/.cache/penguin-lghub/linux"
python3 tools/packaging/linux_sdk.py build \
  --cache "$HOME/.cache/penguin-lghub/sdk" \
  --support-cache "$HOME/.cache/penguin-lghub/linux" \
  --output "$HOME/.local/state/penguin-lghub/sdk-build" --jobs 8
python3 tools/packaging/linux_sdk.py derive \
  --base /absolute/path/to/verified/private-wine \
  --built "$HOME/.local/state/penguin-lghub/sdk-build" \
  --output "$HOME/.local/state/penguin-lghub/portable-wine"

python3 tools/packaging/linux_runtime.py audit \
  --linux-runtime "$HOME/.local/state/penguin-lghub/linux-runtime" \
  --wine "$HOME/.local/state/penguin-lghub/portable-wine" \
  --output "$HOME/.local/state/penguin-lghub/linux-audit"

python3 tools/packaging/linux_probe.py fetch \
  --cache "$HOME/.cache/penguin-lghub/test-tools"
python3 tools/packaging/linux_probe.py prepare \
  --cache "$HOME/.cache/penguin-lghub/test-tools" \
  --output "$HOME/.local/state/penguin-lghub/test-tools"
python3 tools/packaging/linux_probe.py probe \
  --linux-runtime "$HOME/.local/state/penguin-lghub/linux-runtime" \
  --wine "$HOME/.local/state/penguin-lghub/portable-wine" \
  --test-tools "$HOME/.local/state/penguin-lghub/test-tools" \
  --candidate /absolute/path/to/verified/clean-prefix-candidate \
  --output "$HOME/.local/state/penguin-lghub/linux-agent-probe"
```

The SDK build runs with network unavailable, a read-only sysroot, private
`/tmp` and no inherited compiler/loader settings. `build.json` records the
lock hash and resulting modules. Derivation rejects changes to the allowlist,
module hashes or any pinned compatibility artifact. Reflink copying is an
optimization; ordinary copies are valid and hardlinks are not used to modify
the baseline runtime.

The native audit exits nonzero for any unresolved ELF dependency, failed
recorded dynamic-library load, or failed Windows command. Inspect
`linux-audit.json` and `linux-audit.log`. The backend probe first verifies the
candidate and matching/derived runtime, then clones the prefix. It retains
`activation.log`, `agent-verification.json`, `probe.log`, `xvfb.log` and the
API snapshot. Only the test namespace's Wine server is stopped. Owned Xvfb
runs with GLX disabled; no real display, GPU, physical input or external
network is exercised. Pressure-vessel's PID-namespace warning remains in
the logs; the tested invocations nevertheless complete with the requested
process/network isolation.

## Remaining portability work

The current installer builds the custom Wine components, supplies private Linux
libraries and creates one X11/Plasma Wayland launcher. Installation is manually
confirmed on Manjaro, CachyOS and Bazzite; Bazzite uninstall/reinstall and the
shared keyboard-connection repair are confirmed. Native X11/Wayland automatic
profiles and repaired Wayland input are manually confirmed on the Manjaro
workstation. See the [current installer guide](release-installer.md) and
[environment record](../../packaging/tested-environments.json).

Remaining qualification covers the latest packaged profile observer on the
other target desktops, additional device/hotplug combinations, real lock-screen
and compositor restart behavior, and screen-sampled lighting. Non-KWin Wayland,
Flatpak application identities and foreign Wine/Proton prefixes are unqualified.
The three-distribution release scope remains fixed; Debian is future work.
Dependency-use policy and the corresponding-source audit for a hosted binary
runtime are separate release boundaries. No binary runtime is hosted.

The dated measurements below preserve their original scope; current acceptance
supersedes their original pending statuses.

## Upstream references

- [Valve runtime and SDK guidance](https://github.com/ValveSoftware/steam-runtime)
- [Pinned official runtime/SDK publication](https://repo.steampowered.com/steamrt4/images/4.0.20260928.262390/)
- [Official Node 22.21.0 checksums](https://nodejs.org/dist/v22.21.0/SHASUMS256.txt)
- [Debian package archive](https://deb.debian.org/debian/)

## X11 container launcher

`tools/packaging/desktop_runtime.py` creates a separate working prefix from the
verified clean candidate. It does not replace system Wine or the working
workstation prefix. The copied candidate is private test material, not a
redistributable payload. It preserves the recorded compatibility gates and
update exclusion, makes the application directory read-only, and keeps settings
and logs writable. Launches reject Wayland/Xwayland sessions, foreign agents
on port 9010 and unmanaged processes in the selected prefix. Repeated launches
use the existing authenticated reopen endpoint; Stop checks PID/start-time/boot
identity before signaling only that supervisor.

The container is a portability environment, **not a security sandbox**. A real
launch shares the host network, X11 session and devices permitted to the user.
It imports host GPU libraries through pressure-vessel, retains those loader
paths, and adds the pinned non-graphics library directory. A nested application
freeze must use `--dev-bind /dev /dev`: a plain recursive root bind adds `nodev`
and even makes `/dev/null` unusable. This defect was found in cross-host
supervisor testing and corrected in both desktop and headless overlay paths.

The application boundary also keeps Wine on **server synchronization**.
Newer kernels can expose `/dev/ntsync`, which Wine 10.20 selects automatically;
on CachyOS this caused the native CoreMessaging registrar to fail before tray
registration. `server_sync.py` masks that device only inside the application
namespace using an unreadable anonymous file. Host device permissions, other
Wine sessions, graphics and HID access are unchanged. The worker verifies the
policy before starting Wine. A complete quit/relaunch is required for an
existing session to change backend. The UI probe deliberately challenges this
boundary with host NTSYNC when available. See the
[controlled comparison and deployment record](2026-10-27-cachyos-tray.md).

The optional [udev rules](../../packaging/70-penguin-lghub.rules) grant the active
local-seat user HID/USB access for Logitech's vendor ID only. They do not grant
world-write access or add users to a broad input group. Install with sudo, reload
rules, then reconnect the target device or start a new local login. SSH alone
does not confer a local-seat ACL. The runtime does not run as root.

```sh
python3 tools/packaging/desktop_runtime.py prepare \
  --candidate /path/to/verified-candidate \
  --linux-runtime /path/to/private-linux \
  --wine /path/to/private-wine \
  --test-tools /path/to/pinned-test-tools \
  --output /path/to/new-desktop-install

python3 tools/packaging/desktop_runtime.py \
  --config /path/to/new-desktop-install/desktop.json check
python3 tools/packaging/desktop_runtime.py \
  --config /path/to/new-desktop-install/desktop.json install-entry \
  --destination "$HOME/Desktop/Penguin-LGHUB Container Test.desktop"
```

Use the Desktop entry in an X11 login. Its context actions check setup and stop
this installation. There is no user-testing deadline. These development
commands consume verified inputs; they are not yet a complete public installer.

### Independent hosts, October 26

[Recorded results](../../packaging/independent-host-verification.json):

- Manjaro VM, kernel 6.12.77: 46/46 ELF modules, 54/54 dlopen names, Windows
  command, 8/8 factories, 4/4 registry routes and 5/5 agent requests passed.
- CachyOS bare metal, kernel 7.2.9: the same complete set passed.
- An additional owned-Xvfb test started the new supervisor and Electron
  frontend on both hosts, exercised its authenticated status endpoint and
  stopped cleanly. The tray process was observed on CachyOS; it was not
  observed within the short Manjaro sample. This is process/lifecycle evidence,
  not a screenshot or tray-menu acceptance test.
- Both machines have X11 login options installed, with current Wayland sessions
  left running. Neither had Logitech hardware attached during validation.
- **2/2 hosts pass loader/backend validation (100% of this gate); 0/2 have
  completed interactive X11/physical-device acceptance.** Neither percentage
  estimates overall project completion.

A target compiler is no longer necessary for backend probes. On a trusted
build machine, use `linux_probe.py build-activation-probe --output /path/probe.exe`;
transfer that executable and adjacent JSON receipt, then add
`--activation-probe /path/probe.exe` to the target's `probe` command. The receipt
checks source identity and transferred bytes; it does not authenticate an
untrusted publisher. No probe executable or vendor binary is in this repository.

### Fresh-start UI corrections, October 27

The first interactive Manjaro VM attempt exposed defects that the earlier
process/API checks could not detect. The two blank screens contained G HUB's
missing-files error and its repair/download actions. Complete application bytes
alone were insufficient to reproduce first-run installation state.

The corrected recipe now:

1. Retains the 30 verified depot archives under the updater's content-addressed
   cache. The frontend's essential-files check previously reported `cache` missing.
2. Seeds `ProgramData/LGHUBData/applications/applications_db.json` from the
   pinned core depot's `data/applications.json`. This version selects the newer
   cache path when `apps_updater_enabled` is enabled. Without a catalog, the
   offline fresh agent returned no applications and an unnamed default profile.
   The agent now creates its own Desktop application and named default profile;
   no personal database or settings are copied.
3. Activates the already verified color-picker archive in the desktop clone.
   Previously the new launcher left that generated archive unused.
4. Initializes the ten existing bundled font faces after the stylesheet has
   loaded and before starting the UI application. Initial CSS loads failed in
   this Wine/Electron combination, while explicit loads of the same bundled
   bytes succeeded. This is a targeted startup workaround; the precise underlying
   Wine/Chromium failure is not yet established. Only `index.html` changes in this
   additional transformation, and all 267 packed entries are checked. The existing
   color fix, font files and other resources remain byte-identical. No Windows
   fonts, font substitutions, system package upgrades or sandbox-disable switch
   are required by this tested frontend path.

[Machine-readable result](../../packaging/first-run-ui-verification.json): a
new candidate built by the corrected recipe verified 1,605 managed files, then
passed **4/4 startup checks (100% of this gate)** in the Manjaro VM. The captured
UI reached the readable first-run privacy prompt. The probe did not click it,
choose privacy preferences, connect to the live desktop or expose physical
devices. It used the pinned Linux runtime, private Wine and an owned Xvfb in a
private network/PID namespace. The existing live VM installation stayed intact.
All 83 packaging unit tests passed. This result does not qualify host GPU,
tray interaction, peripheral behavior, all languages/fonts or CachyOS UI startup.

Subsequent VM testing found a separate native-tray font prerequisite. The
recipe now supplies a per-prefix Segoe UI replacement using DejaVu Sans from
the pinned Linux runtime, and generated launchers install the project icon.
See the [desktop regression investigation](2026-10-27-desktop-regressions.md)
for the controlled tray result, configuration checks, and the still-open
black-window report. The original four startup checks were a narrower gate.

The October 27 CachyOS tray investigation extended this command to require a
running native tray process and an XEmbed dock request on an owned tray host,
with GLX enabled on the private Xvfb. The corrected candidate passed **6/6**
checks. A deliberately disabled tray still passed the original four checks but
failed both additions and returned a failing overall status. This validates the
new failure detector; it does not certify a physical GPU or Plasma menu input.
The generated frontend now retains native-tray errors and logs its exit status,
instead of directing those diagnostics to `/dev/null`. Details and the remaining
live failure are in [the CachyOS tray report](2026-10-27-cachyos-tray.md).

The new repeatable gate is:

```sh
python3 tools/packaging/linux_ui_probe.py probe \
  --candidate /path/to/newly-generated-candidate \
  --linux-runtime /path/to/private-linux \
  --wine /path/to/private-wine \
  --test-tools /path/to/pinned-test-tools \
  --output /path/to/new-ui-probe
```

The result and screenshot are `ui-verification.json` and `first-run.png` in the
private output directory. Use an idle runtime or a separate verified runtime
clone if a live session holds its lock. The test refuses to share live input
devices or a desktop connection. A separate fresh desktop clone is still needed
for user acceptance; do not promote a probe's modified settings database.

Keep the verified depot installation path. The native installer could supply
some missing setup state, but is not necessary to pass this reproduced startup
case and would not itself supply the font workaround. Deferred optional SDK and
integration installer actions remain separate qualification work. Updater remote
execution still reports `NOT_READY` under the frozen/offline test; this does not
prevent reaching first-run setup and is not presented as resolved.

## Bazzite test environment recommendation (research only)

Use a **QEMU/KVM virtual machine** for the later Bazzite qualification phase.
A Distrobox is useful for userspace packaging checks but does not reproduce
Bazzite's own boot, kernel, SELinux policy, atomic deployment/update behavior or
Wayland desktop. The [Bazzite Distrobox guide](https://docs.bazzite.gg/Installing_and_Managing_Software/Distrobox/) explicitly describes containers as minimal distribution subsystems, not VMs.

The [official hardware requirements](https://docs.bazzite.gg/Gaming/Hardware_compatibility_for_gaming/) specify UEFI, 8 GB RAM, four CPU cores,
50 GB minimum storage and 120 GB recommended storage; gaming graphics requirements
are a separate limitation for a virtual GPU. The [installation guide](https://docs.bazzite.gg/General/Installation_Guide/install-guide/) provides
the official image-selection/checksum workflow. Do not select a host-NVIDIA
image merely because the machine running QEMU has NVIDIA: the guest normally
sees a virtio GPU.

Proposed VM: KDE desktop image (no Steam Gaming Mode), UEFI/OVMF, four vCPUs,
8–12 GiB RAM, a **120 GiB sparse qcow2** disk, virtio storage/network, and
SPICE plus virtio display. Start without physical USB passthrough. Enable
virtual 3D only where the host/guest driver combination supports it. A virtual
GPU can test installation, desktop integration and Wayland behavior; it cannot
certify bare-metal NVIDIA/AMD rendering. USB passthrough of a spare Logitech
device is a later explicit test, not an automatic detachment from the host.

Storage planning estimates, not measurements of an installed Bazzite VM:

| Choice | Planning allowance | Fidelity |
|---|---|---|
| Minimal Distrobox with test dependencies | About 5–15 GiB, varying by image/packages | Userspace only; cannot qualify Bazzite or solve Wayland key injection |
| Bazzite KVM guest | 120 GiB virtual capacity; budget at least 60–80 GiB actual free space initially, preferably 120 GiB | Actual OS/login/update/security policies; virtual graphics |
| Bazzite on separate SSD | Official 120 GB recommendation plus test payloads | Best final graphics/device validation; more setup |

The VM budget includes a provisional installer-download allowance, deployed OS,
updates/rollback growth and our test payload. Exact ISO bytes and initial disk
use must be measured after selecting the image; qcow2's virtual capacity is
not preallocated physical use, and snapshots can grow substantially.

At this check, the main workstation had about 39 GiB free, so do not allocate
this test there yet. The remote CachyOS disk had about 881 GiB free, but that
machine has only 8 GiB RAM total. An image stored on that host and served over
NFS to local QEMU is technically possible, but introduces network-storage
behavior into the experiment. Prefer freeing space on a local SSD first, then
install, record the image digest, snapshot the clean guest, and run the same
loader/backend probes before user-driven desktop tests. No Bazzite download,
VM or container was created during this pass.
