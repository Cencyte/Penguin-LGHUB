# Installation

The installer provides the complete source-to-launcher entry point.
It downloads pinned inputs, builds the changed Wine components and creates a
fresh application prefix. No pre-existing lab build, donor prefix, host Wine,
Docker daemon or host MinGW compiler is an input to this workflow.

Boot the target OS and run these commands in its normal KDE desktop terminal:

```sh
git clone https://github.com/Cencyte/Penguin-LGHUB.git Penguin-LGHUB-release
cd Penguin-LGHUB-release
./install.sh
```

Installer 0.3.9 defaults to installation when no action flag is supplied, including when only options such as `--verbose` are given. `--install` remains accepted; `--check`, `--uninstall` and `--help` keep their explicit behavior.

A [source ZIP release](https://github.com/Cencyte/Penguin-LGHUB/releases/tag/v0.9.0-preview.7) is also available. Extract it, open a terminal in its folder, and run `./install.sh`. If extraction drops executable permissions, run `bash install.sh` instead. No Git metadata is needed.

Preview 7 includes installer 0.3.9, automatic X11/Wayland profile observers, the shared keyboard connection, explicit profile prerequisites and payload verification, and receipt-scoped removal. It retains the untimed pause before sudo authentication and the Xwayland native-input route check. The window restore corrections now run in X11 as well as Wayland, fixing the session-specific omission exposed by the Manjaro VM. This changes the private launcher environment; it requires no host Wine, graphics-driver, or compositor changes. Reinstall acceptance and Bazzite/CachyOS regression checks for this revision remain pending.

The release-consolidation run rebuilt the current Wine patches and supplements in the pinned SDK, then prepared a new prefix with host Wine hidden. All 8 activation checks, 4 registry checks and 6 isolated UI/tray checks passed; the combined project suite passed 239 tests. The profile payload gate also verified the adapter, pinned agent and both observer backends. These automated checks complement the recorded manual confirmations; they do not establish fresh packaged-profile acceptance on CachyOS or Bazzite. [Validation record](release-installer-verification.json).

The repository currently requires GitHub access while it remains private.
Run as your regular user. The installer requests sudo for its small Logitech
device-rule helper; it never starts G HUB as root. It installs one
**Penguin-LGHUB** Desktop launcher and an application-menu copy, with the
project icon and Quit/check/log actions. Both launch paths share the same Wine
runtime, prefix and G HUB settings. Each launch selects X11 or KDE Plasma Wayland
from the current login session; logging out and changing sessions does not
require another installation. Quit G HUB before switching sessions.

Installer 0.3.9 pauses without a countdown when its device-rule step needs a
password. Press Enter when ready; sudo then shows its normal password prompt.
The pause also applies to removal when authorization is needed. Cached or
passwordless sudo needs no pause. Passwords are handled by sudo, and system
authentication policy is unchanged. If authorization never starts the rule
helper, rollback confirms the rule is absent and skips unnecessary sudo cleanup.

Check setup inspects the current session. Wayland keeps its Xwayland, KWin,
portal and user-consent requirements; automatic selection does not grant input
permission or fall back to X11 when a Wayland capability is missing.

For diagnostics, `desktop_runtime.py` accepts:

- `--session auto` — select the login session on each launch (the default).
- `--session x11` or `--session wayland` — require the selected session explicitly.
- `--experimental-wayland` — retained as a legacy alias for `--session wayland`.

Bazzite's intended test is its default Plasma Wayland session; setup does not
add an unsupported full-X11 desktop.

Initial scope: x86-64 Manjaro, CachyOS and Bazzite KDE. Use native Linux storage
(such as ext4, Btrfs or ZFS) and reserve **31 GiB free**
for the pinned source installer. This is an offline capacity budget, including
filesystem overhead and 2 GiB of free headroom. Verified downloads, build tools
and test copies are removed after their final use. This is working space, not
the download size or final application size. A failed installation rolls back;
a new attempt starts clean. To choose another storage location, add:

- `--directory /path/to/spacious/local/storage` — store the runtime, prefix and build files here.

Setup and `--check` create and remove a small temporary filesystem probe before
acquisition. It verifies directory-relative symlinks, hardlinks, renames and
private permissions used by the runtime. A tested NTFS3 volume rejected
`symlinkat` despite passing runtime file-hash verification; it now produces an
early error requesting another `--directory`. No storage is reformatted.

Keep that directory after installation: it contains the actual runtime and desktop prefix.

The capacity calculation is recorded in
[`packaging/install-storage.json`](../../packaging/install-storage.json) and bound
to the seven acquisition/runtime lock files. Changed pins require a budget review.
It uses the pinned operations and completed Bazzite artifacts, with files rounded
to 4 KiB and every hardlinked/reflinked copy counted separately. Component budgets
allow for compilation scratch, temporary replacements and prefix growth. The
largest planned stage is DLL reconstruction: **26.625 GiB**, including the SDK,
Linux libraries, Wine, helpers, both media downloads and reconstruction work.
Adding **2 GiB for filesystem overhead** and **2 GiB left free**, then rounding
up, gives **31 GiB**. This is a conservative offline planning bound, not a measured
live peak or a filesystem quota; concurrent unrelated writes can still exhaust
space. Lower-level output/download checks retain the same 2 GiB floor.

| Stage | Budget for simultaneously present files |
|---|---:|
| Toolchain extraction | 8.125 GiB |
| Wine compilation | 11.125 GiB |
| Linux libraries and helpers | 16.625 GiB |
| Windows DLL reconstruction | 26.625 GiB |
| G HUB prefix preparation | 17.125 GiB |
| Loader verification | 20.625 GiB |
| UI/tray verification | 23.625 GiB |

The earlier completed installation contained 28.8 GiB of files, including retained
build/test material; that observation was not a live peak measurement. Its active
runtime, prefix, helpers and implementation source contained approximately 4.4 GiB
with hardlinks counted once. Successful installation now removes disposable work,
while retaining small reports under `provenance/`, central logs, `work/built-wine`,
and G HUB's required depot cache inside the active prefix. Cleanup is part of the
transaction: an error still rolls back the new installation. Existing committed
installations are never automatically pruned. Standalone development helpers keep
their outputs for inspection; automatic disposal belongs to `install.sh --install`.

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

No arguments starts installation; `--help` prints usage without installing. Mode-0600 command logs and host reports are retained
under `${XDG_STATE_HOME:-~/.local/state}/penguin-lghub/`, outside the removable
installation. Failed-transaction receipts are retained there too. This preserves
the reason for a failure after rollback removes downloaded and generated payloads. Do not publish raw logs without checking them for
local paths, usernames, device identifiers and application state.

Readiness checking and installation both show separate headers for system,
desktop, tools, device access, security policy and namespace checks. Wayland
adds its Xwayland/KWin/portal check. A separate profile check verifies the observer libraries and, on Wayland, KWin operation mode and scripting availability without loading a script. Labels and descriptions start in consistent
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
3. Apply all 46 Wine patch topics to Wine 10.20 source. Build every changed
   module, the supplementary DLL implementations and the activation probe.
4. Prepare the pinned Linux userspace and bundled Node/display-test helpers.
5. Obtain official Microsoft media and reconstruct the two pinned DLLs in an
   isolated workspace. Verify their exact final hashes.
6. Obtain the 30 selected Logitech depots from Logitech, verify and extract the
   application, generate registry/service/depot state, and apply resource fixes.
7. Prepare the desktop prefix with fonts, frontend fixes, frozen application
   and depot state, server synchronization and integrated startup/restore fixes.
8. Verify both native-profile backends, the pinned agent and source-built adapter.
   Check native/dynamic libraries, a real Windows command, first-run UI/fonts
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
| Automatic profiles | Host session, X11/libdbus libraries and KWin capabilities | Host observer resolves the executable; a private authenticated mailbox feeds the adapter built into `mfsensorgroup.dll`. Both backends are installed. |
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
The same removal path applies on Manjaro, CachyOS and Bazzite. The supervisor stops the profile observer and unloads its temporary KWin script before releasing the launch lock. Observer modules, the adapter and any remaining session mailbox are inside the managed directory and are removed with it. No permanent KWin configuration is installed.

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
installation on Bazzite has now been reported successful, including an
uninstall/reinstall cycle. The delayed/stuck G502 X Plus key-binding defect was
subsequently confirmed fixed on booted Bazzite after patch 0046.

Preview 3 / installer 0.3.2 adds patch 0046. Wine now uses one serialized,
process-wide XTEST output connection for G HUB, retaining it across worker
thread exits. On Xwayland, separate connections represent separate emulated
keyboards; a release on a different connection can leave the original key
held. The [regression receipt](host-keyboard-threads-verification.json) records
before/after tests. This fix is built by the normal installer, with no manual
DLL copying or new host dependency. Existing installations are preserved by
the installer; installing the new preview into an occupied directory requires
the documented backup/uninstall workflow first.

The observed one-time Bazzite permission prompt was KWin input authorization.
Its per-application approval for `lghub_agent.exe` survived uninstalling G HUB,
as intended. Setup neither clears that approval nor enables a global bypass.

The Bazzite confirmations establish installation/removal and the reported mouse
binding fix. They do not automatically complete every device/reconnect, macro,
G-Shift, profile, lighting, tray and relaunch check. Fresh installation on
CachyOS and the Manjaro VM was also manually confirmed on October 31, completing
installation checks on all three target distributions. That confirmation does
not separately certify removal, session switching or the new packaged profile adapter on those installations. KDE may request
input-control authorization when an assignment is used. Setup does not approve
that permission. Automatic X11 and native Wayland application profiles are now manually confirmed on the Manjaro workstation. Screen-sampled lighting remains unqualified.

### Native application selection and profile tests

The pinned G HUB application picker filters for `*.exe`, so extensionless
Linux programs such as `kate` are hidden. The correct path depends on the launcher:

| Launcher | Host Kate path in Wine's file picker |
|---|---|
| Installed **Penguin-LGHUB**, using the packaged runtime | `Z:\run\host\usr\bin\kate` |
| Legacy **G HUB (Tested Wine)**, using host libraries | `Z:\usr\bin\kate` |

The packaged runtime has its own `/usr/bin` and exposes the host's under
`/run/host/usr/bin`. The legacy launcher exposes the host root directly; it
does not create `/run/host`. Enter the complete path in the **File name** field
from a small folder instead of browsing into `/usr/bin`. An isolated Wine 10.20
dialog accepted the extensionless Kate path this way, while directory browsing
stalled during shell filtering and repeated `desktop.ini` lookups. This is a
workaround, not a repaired browser. No renaming or copying of Kate is needed.
File selection/application registration and automatic profile matching are
separate checks; selecting an executable does not establish focus detection.

To test automatic switching, enable it in G HUB, disable a forced persistent profile, and assign different harmless keys to a spare button in two application profiles. Focus each application and confirm both the selected profile and emitted key change. Return to an unmatched application to check the Desktop profile.

The native observer uses X11 foreground properties or a temporary, current-user KWin focus script. Executable identity is resolved on the host, outside the runtime container. Its authenticated mailbox feeds G HUB's original typed application-event/profile logic through the source-built adapter. Script shutdown and mailbox cleanup are part of the launcher lifecycle and uninstall tests. Manjaro X11/Wayland behavior is manually confirmed; packaged-profile acceptance on CachyOS/Bazzite remains separately tracked. Flatpak identities and foreign Wine/Proton prefixes are unqualified.

The [Bazzite readiness findings](bazzite-readiness.md) explain the remaining
host-policy/driver boundaries. Existing [third-party use questions](third-party-use.md)
are unchanged by local extraction or containerization; the repository contains
no Microsoft or Logitech payload binaries.
