# Installation

The installer provides the complete source-to-launcher entry point.
It downloads pinned inputs, builds the changed Wine components and creates a
fresh application prefix. No pre-existing lab build, donor prefix, host Wine,
Docker daemon or host MinGW compiler is an input to this workflow.

Boot the target OS and run these commands in its normal KDE desktop terminal:

```sh
git clone https://github.com/Cencyte/Penguin-LGHUB.git Penguin-LGHUB-release
cd Penguin-LGHUB-release
./install.sh --install
```

A [source ZIP release](https://github.com/Cencyte/Penguin-LGHUB/releases/tag/v0.9.0-preview.2) is also available. Extract it, open a terminal in its folder, and run `./install.sh --install`. If the archive tool drops executable permissions, run `bash install.sh --install` instead. No Git metadata is needed by the installer.

The repository currently requires GitHub access while it remains private.
Run as your regular user. The installer requests sudo for its small Logitech
device-rule helper; it never starts G HUB as root. It installs a launcher for
the current session in the resolved Desktop and application-menu directories,
with the project icon and Quit/check/log actions. For the other session on an
OS that offers both, use `desktop_runtime.py install-entry` with the installed
`desktop/desktop.json` configuration. For a Wayland launcher, add:

- `--experimental-wayland` — enable the Plasma/Xwayland launcher path.

Bazzite's intended test is its default Plasma Wayland session; setup does not
add an unsupported full-X11 desktop.

Initial scope: x86-64 Manjaro, CachyOS and Bazzite KDE. Reserve **80 GiB free**
for the first build, downloaded media, extracted depots, verification copies and
20 GiB of retained headroom. This is a working-space requirement, not a download
size. A failed installation removes its downloads; a new attempt starts clean after rollback. To choose another storage location, add:

- `--directory /path/to/spacious/local/storage` — store the runtime, prefix and build files here.

Keep that directory after installation: it contains the actual runtime and desktop prefix.

Host prerequisites are Python 3.12+, bubblewrap, zstd, sudo, udev, XDG user
directories, gdbus and kdialog, with authenticated local X11/Xwayland access;
Wayland checks also use xdpyinfo and the KDE remote-desktop portal. These are
checked before acquisition. No host package transaction or system upgrade is
performed. Missing prerequisites produce a named error rather than silently
changing an immutable image or a version-pinned workstation. OpenSSL libcrypto
is used for the encrypted Logitech resource depots.

Standalone commands:

- `./install.sh --check` — record host readiness and explain blockers.
- `./install.sh --help` — show usage, switches and examples.
- `./install.sh --uninstall` — remove the managed installation and its G HUB settings.

Optional installation switches:

- `--verbose` — display command output beneath each colored step.
- `--jobs 4` — select 1–16 Wine build jobs; the default is 4.

No arguments prints only usage. Mode-0600 command logs and host reports are retained
under `${XDG_STATE_HOME:-~/.local/state}/penguin-lghub/`, outside the removable
installation. Failed-transaction receipts are retained there too. This preserves
the reason for a failure after rollback removes downloaded and generated payloads. Do not publish raw logs without checking them for
local paths, usernames, device identifiers and application state.

Readiness checking and installation both show separate headers for system,
desktop, tools, device access, security policy and namespace checks. Wayland
adds its Xwayland/KWin/portal check. Labels and descriptions start in consistent
columns; ellipses immediately follow the description text. Only `--check` adds
aligned `OK`/`FAIL` results. An `OK` records no blocking error in that check, not completed hardware
acceptance. Verbose mode also prints the saved host
report; ordinary mode keeps the headers, warnings and blockers visible.

During installation and removal, an ASCII spinner appears on the line below
the latest output. It pauses for sudo authorization, is excluded from logs,
and is omitted for redirected output or terminals without cursor controls.
The opening version block includes the project, installer and pinned Logitech
G HUB versions, separated from subsequent output by blank lines. A successful
operation ends with `🎉 Installation complete! 🎉` or `🎉 Uninstall complete! 🎉`.
Generated desktop and application-menu entries are named **Penguin-LGHUB**.

The project regression runner and GitHub CI use the same fixed-color headers:

```sh
python3 tools/packaging/run_project_tests.py --run
```

- `--verbose` — show individual results and captured test output.
- `--log-file /path/to/new-tests.log` — write a new log at a chosen path.
- `--help` — show usage and examples.

Complete test output is logged in both modes. Failure details remain visible
in ordinary mode, and failures produce a nonzero exit status. Direct unittest
commands remain available with their standard output for contributor tooling.

The logo is pre-rendered by Chafa into verified Kitty, sixel and symbol streams.
Thus it is the first installer display without installing Chafa on the host.
Konsole 22.04+ uses the RGBA Kitty stream because its sixel canvas fills transparent
areas with black. Terminals using sixel retain its transparency request; unknown
terminals use symbols. Kitty/sixel artwork is 200×200 pixels; the 20×10-cell symbol fallback approximates
that size using the terminal's font metrics. Unknown terminals and multiplexers
use symbols. Terminal font size controls the normal-weight white version/log
lines. Category colors are fixed in `packaging/installer.json`.

## What the command performs

1. Inspect OS/session, namespaces, portals, drivers, device ACLs and SELinux.
2. Download and verify the private Steam Runtime SDK, compiler packages and
   stock Wine base. Extract them privately without package-maintainer scripts.
3. Apply all 45 Wine patch topics to Wine 10.20 source. Build every changed
   module, the supplementary DLL implementations and the activation probe.
4. Prepare the pinned Linux userspace and bundled Node/display-test helpers.
5. Obtain official Microsoft media and reconstruct the two pinned DLLs in an
   isolated workspace. Verify their exact final hashes.
6. Obtain the 30 selected Logitech depots from Logitech, verify and extract the
   application, generate registry/service/depot state, and apply resource fixes.
7. Prepare the desktop prefix with fonts, frontend fixes, frozen application
   and depot state, server synchronization and integrated startup/restore fixes.
8. Check native/dynamic libraries, a real Windows command, first-run UI/fonts
   and native tray registration in an isolated display. Inspect offscreen host
   graphics import and audio connectivity without application windows or sound.
9. Install the vendor-scoped udev rules and the desktop/application-menu entry.

The build recipe is reproducible from public source and pinned downloads;
**byte-for-byte deterministic rebuilds across machines are not claimed**.
Unchanged Wine modules come from the pinned Arch Wine package. The SDK and
Debian packages are private extracted build tools, not packages installed into
the target OS. A compiled runtime release is not hosted by this source workflow;
the corresponding-source/notice audit remains required before such a release.
The source build's SHA-256 inventory is recorded and checked before use.

The Windows reconstruction engine defaults to bubblewrap on all three OSes.
For existing rootless Podman installations, add:

- `--engine podman` — select Podman for the Windows-resource acquisition step.

The source build and application runtime still use bubblewrap/pressure-vessel.
Podman needs working rootless user-namespace mappings. On SELinux hosts, only
the dedicated private toolchain/work directories are labelled for container
access. There is no Docker requirement, privileged container, host-home relabel,
or SELinux-disable step. **Distrobox is not part of this implementation.**

## How host-specific behavior is selected

`install.sh` delegates to one Python orchestrator. The same ordered pipeline builds
the private Wine/Linux runtime and prepares G HUB on every supported distribution.
Each step calls a focused helper and records successful completion for retries.

| Decision | Input | Behavior |
|---|---|---|
| Supported OS and package inventory | `/etc/os-release` → shared `host_policy.py` | Manjaro/CachyOS use pacman inventory; Bazzite uses RPM inventory. Installing a second package manager does not change the choice. No host package transactions. |
| Desktop/input path | Actual session and capability probes | X11 launcher, or KDE Wayland via Xwayland, authenticated display, KWin and input portal. |
| Windows-resource acquisition | Explicit engine switch | Bubblewrap by default; existing rootless Podman when requested. |
| Container labels | SELinux enforcing + Podman selected | Label only private acquisition directories; preserve host policy. |
| Graphics, sound and devices | Driver/socket/device inspection | Import host graphics/audio through the private runtime and use seat-based Logitech access. |
| Desktop/menu paths | XDG directory settings | Register launchers and record their exact paths and hashes. |

This separates a small distribution-policy table from capability checks and reusable
installation steps. The ownership receipt also lets removal operate when the
original session or host prerequisites are unavailable. Future OS support should
extend the policy and its tests rather than copy the installation pipeline.

## Removal

Run from the extracted source ZIP or checkout:

```sh
./install.sh --uninstall
```

- `--directory /path/to/installation` — select the same custom directory used during setup.
- `--verbose` — display helper output beneath the removal step headers.

**Removal deletes G HUB profiles/settings in the managed prefix. Export or back up
anything you want to keep first.** The dedicated directory, private runtime, build
cache, downloads, icon and embedded runtime logs are removed. Installer transaction
diagnostics are retained separately. Receipt-recorded Desktop
and application-menu launchers are removed only if their content still matches.
Each installation has a separate udev rule, scoped to its user and installation ID;
sudo removes only that rule and reloads udev without retriggering unrelated devices.
The same removal path applies on Manjaro, CachyOS and Bazzite.

Removal signals only a supervisor whose PID, start time, boot ID and command match
this installation, then waits for its launch lock. Unexpected prefix processes,
changed launchers or nested mounts stop removal with a specific explanation.
Incomplete installations can be removed too. If file cleanup fails, the receipt
is retained for a retry. Successful removal leaves no new uninstall log; failure
logs are saved in the independent diagnostic directory printed on screen.

The source ZIP/checkout, separately requested compatibility/test reports, pre-existing
shared rules, host packages and other Wine installations remain. KDE input permission
is user-authorized desktop state, not an installer-created file. Older development
installations without the new ownership receipt require separate cleanup review.

## Transactions and recovery

Setup has three phases: prepare privately, verify, and commit. The durable
installation receipt marks the commit only after all required payload checks,
device-rule registration and launcher creation succeed. Payload filesystems are
flushed before the durable commit record, so reported writeback errors trigger
rollback. Managed launchers refuse
to start an uncommitted installation. The existing installed application is never
replaced as part of an attempted new installation.

Any caught failure, Ctrl-C, SIGTERM or terminal hangup rolls back the attempt:
private downloads, SDK, built runtime, prefixes and recorded launchers are removed,
and its individual udev rule is removed/reloaded if that step was attempted.
Only diagnostic logs, host reports and a transaction record remain. An empty
installation directory that existed beforehand is restored to empty. Choose an
existing parent directory when using custom storage.

A power loss or SIGKILL cannot execute cleanup. The next install run detects its
incomplete receipt, rolls that transaction back, then starts fresh. This also
recovers incomplete installations from preview 1, preserving their older logs.
An already committed installation is left unchanged; explicitly uninstall it
before replacing it. No automatic package upgrade or rollback of unrelated
software is performed.

If cleanup itself fails—for example sudo is denied, storage becomes inaccessible,
a file has been externally changed or an unexpected process still holds the
prefix—the command reports **rollback incomplete**, retains its journal and exits
unsuccessfully. It never reports success with a partial installation. Correct the
reported obstacle and rerun the installer to recover. This is journalled rollback
across the managed files, not an atomic snapshot of the entire OS or a guarantee
that cleanup can run while the machine has no power.

### Bazzite first installation failure

The first booted attempt passed host checks and private SDK preparation, then
stopped while downloading Wine source. GitHub supplied a valid chunked HTTP
response without `Content-Length`; the previous downloader rejected it.
The downloader now accepts that response while enforcing the pinned final byte
count and SHA-256. A fresh download reproduced all **53,765,986 bytes** with hash
`b53683fb89b891fa1d5e3c7ca8c2f7f023ee0b6e397edc6084fb75519eb047df`.
This is a general HTTP handling bug that could affect any supported OS.

## Verification boundaries

The build and isolated UI/tray checks passed on the development host; rootless
Podman and bubblewrap independently reproduced both Windows DLL hashes.
See [the measured validation record](release-installer-verification.json).
The assembled top-level workflow passed all 16 payload/verification stages in a
fresh private directory, using reverified download caches. Host-rule installation
and live desktop promotion were deliberately excluded; both launcher variants
were generated separately in a private directory. A complete
unassisted clone-to-device installation on Bazzite is **still the next user
acceptance test**, not a result inferred from these checks.

Bazzite acceptance requires running the public workflow and testing device
discovery/reconnect, key bindings in a native Wayland app, macros, G-Shift,
profile changes, ordinary lighting, tray actions and relaunch. KDE may request
input-control authorization when an assignment is used. Setup does not approve
that permission. Automatic profile detection of native Wayland applications and
screen-sampled lighting remain separately scoped limitations.

The [Bazzite readiness findings](bazzite-readiness.md) explain the remaining
host-policy/driver boundaries. Existing [third-party use questions](third-party-use.md)
are unchanged by local extraction or containerization; the repository contains
no Microsoft or Logitech payload binaries.
