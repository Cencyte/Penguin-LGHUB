# CachyOS physical-machine installation, October 27

> Historical investigation record. For current installation, X11/Wayland profiles and validation status, see the [installation guide](release-installer.md) and [recorded environments](../../packaging/tested-environments.json).

**October 28 update:** a separate opt-in Wayland installation is now prepared
with the current Manjaro-tested runtime fixes. See the final section below;
the original X11 installation remains available.

The local installer at `6cce5ca` includes the fresh-start fixes absent from the
published source snapshot `2afdb98` (development source `b76e404`). The changes
retain the verified depot cache, initialize the bundled application catalog,
activate the color-picker and frontend font fixes, configure the missing native
tray font fallback, verify behavior flags, and install the launcher icon.

## Prior research and testing

The comparison covered X11 versus Wayland, rolling library ABI differences,
optimized CPU packages, graphics-driver import, local-seat HID permissions,
user/desktop paths, helper-runtime versions, and local IPC. The pinned Wine and
Linux userspace keep the application separate from system Wine; pressure-vessel
imports the host graphics stack rather than packaging one machine's GPU driver.

The October 26 [independent-host results](../../packaging/independent-host-verification.json)
already include CachyOS bare metal: **46/46 ELF links, 54/54 dynamic loads,
8/8 activation factories, 4/4 registry routes, and 5/5 agent API requests**.
Supervisor start/control/stop and GL/EGL/Vulkan library import also passed.
These checks did not establish visible desktop or physical-device acceptance.

Earlier preparation installed matching X11-session packages and the narrowly
scoped Logitech `uaccess` rule. This pass retains those; it does not upgrade
packages or change the currently active Wayland session.

## Prepared test installation

Target: CachyOS kernel `7.2.9-1-cachyos`, physical GeForce GT 730 system.
The X11 session packages and desktop entry were installed; the Ly menu had not
yet been refreshed (see the follow-up below). The signed-in session at preflight
was Wayland, with no Logitech USB peripheral connected. Available disk space
was approximately 880 GiB after the transfer.

Private paths on that machine:

- Source: `~/Source2/Penguin-LGHUB-cachyos-6cce5ca/`
- Candidate: `~/.local/state/penguin-lghub-validation/candidate-003/`
- User installation: `~/.local/state/penguin-lghub-validation/desktop-20261027/`
- Isolated UI check: `~/.local/state/penguin-lghub-validation/ui-20261027/`

The transferred candidate was produced by the clean-prefix recipe from verified
inputs, not cloned from the owner's personal working prefix. All **1,605 managed
files** verified on the target. All **84 packaging tests passed** on CachyOS.
The desktop recipe then creates a separate working prefix, repairs relocated
user-folder links and activates the verified frontend changes. The tray fallback
is applied before starting the agent/UI on first launch.

The [isolated UI result](../../packaging/cachyos-ui-verification.json) passed
**4/4 checks (100% of this startup gate)**: required files, ten loaded bundled
font faces, the Desktop/default profile, and a readable first-run prompt.
The test used the pinned runtime, with 130 mapped private Wine images, without
the live desktop, external network or physical devices. It stopped cleanly.

The user-facing entry is **Penguin LGHUB — CachyOS Test**, installed on the
Desktop and in the applications menu. Its logo is stored with this installation.
The duplicate Container Test desktop shortcut was subsequently removed at the
owner's request; its original entry is retained beside the older installation. Both installations'
prefixes remain intact; no live G HUB session was running during preparation.

This is a deployment of the current development installation process with
previously acquired and verified inputs. It is not a demonstration that an
unassisted user can download only the current GitHub snapshot and complete every
acquisition/build/install step.

## Acceptance boundary

On Manjaro, the owner has now reported that restarting clears the black window
and tray integration seems fine. This does not establish the exact black-window
cause or confer device/graphics qualification on CachyOS.

CachyOS live acceptance still requires the owner to select Plasma (X11), attach
the intended Logitech device, and test visible UI, restore, tray actions, device
discovery, bindings, macros, profiles, G-Shift and lighting. No remote GUI input
or live-session restart is used to substitute for those checks. Bazzite,
Wayland implementation and disk cleanup are outside this pass.

## Ly session discovery follow-up

The owner reported that Ly offered `xinitrc`, but no Plasma X11 session. The
installed packages were already correct: `plasma-x11-session 6.7.5-1`,
`kwin-x11 6.7.5-1`, `xorg-server 21.1.24-1.1`, `xorg-xauth 1.1.5-1.1`, and
`ly 1.4.1-1.1`. Package integrity checks for Ly, the Plasma X11 session, KWin X11
and Xorg reported zero altered files. `startplasma-x11`, `kwin_x11` and `xauth`
resolved their shared libraries. `/usr/share/xsessions/plasmax11.desktop` names
**Plasma (X11)** and executes `/usr/bin/startplasma-x11`; Ly's `xsessions` points
to that directory.

The active `ly@tty2.service` process had started on October 8, before the X11
packages were added on October 26. [Ly 1.4.1's startup code](https://github.com/fairyglade/ly/blob/v1.4.1/src/main.zig)
crawls session directories before entering its event loop; logging out alone
does not reconstruct that list. This explains the missing option despite the
installed session file. The preceding `xinitrc` login attempt had exited, and
this user has no `~/.xinitrc`; that option is not the Plasma session.

At 13:03 EDT on October 27, refreshed **only the idle Ly service**, after checking
that no local-seat session remained open and that Ly had no child processes.
The old PID 625 was replaced by PID 49158. The new service is active and enabled;
its startup log has no session-crawl error. No packages, system configuration,
session files or live desktop were changed by this repair.

At the Ly login screen, select the session row with Up/Down (or Tab), use
Left/Right to select **Plasma (X11)**, then log in normally. Ly is on tty2
(`Ctrl+Alt+F2` if needed). Actual Plasma X11 login and physical G HUB behavior
still await the owner's test; a healthy greeter and dependency checks do not
establish those results.

## October 28: current Wayland fixes deployed for owner testing

The target had only the user-facing X11 launcher. Earlier CachyOS Wayland work
had tested keyboard transport in isolated sessions; it had not installed a
complete Wayland G HUB desktop launcher. Its active session was still Plasma
X11 during this deployment, and it was not switched remotely.

The new source snapshot is `~/Source2/Penguin-LGHUB-cachyos-wayland-20261028`.
The separate user installation is
`~/.local/state/penguin-lghub-validation/cachyos-wayland-desktop-20261028`.
It is prepared through the current installer from the existing verified
`candidate-003`; settings in the working X11 prefix remain untouched. The new
prefix has its own first-run choices and settings. Wine, the Linux runtime and
the pinned Node tools are reused; no package or graphics driver was upgraded.

Included behavior: frozen installation depots, bundled frontend fonts and tray
font fallback, color-picker repair, native-tray logging, the CachyOS server-sync
boundary, explicit Xwayland selection, work-area/restore-state correction,
accepted graceful closing, bounded desktop initialization and frontend startup
recovery. The current frontend hash and both optional Wine-module hashes match
the tested Manjaro candidates. The owner has declared the remaining cosmetic
windowing anomalies out of scope; this deployment does not claim to solve them.

`wayland_launcher_probe.py` now accepts the same two verified Wine overlays as
the installer and exposes host NTSYNC to its outer private namespace. The
production application boundary must mask it before Wine starts; the worker
records that server synchronization is enforced. This preserves the earlier
CachyOS tray finding instead of hiding it behind a synthetic `/dev`.

Measured on CachyOS: **126/126 packaging tests** and **2/2 complete isolated
Wayland startup/shutdown cycles** pass (100% of each named test set). Both
cycles passed required-file, bundled-font, Desktop/default-profile and readable
first-run checks, registered one tray icon, confirmed the NTSYNC mask, exited
cleanly, left no prefix processes and preserved the managed file hashes.
The first unit invocation lacked `node` on the SSH PATH; rerunning with the
already packaged Node 22.21.0 fixed those eight test-environment errors without
installing a system dependency.

The integration test uses private KWin/Xwayland, software rendering, D-Bus,
network and device namespaces. No live desktop was driven or captured, no input
permission was granted, and no physical device was used. This does not qualify
the GT 730's physical Wayland graphics performance. The host's older NVIDIA
470 stack remains a separate limitation, as recorded in the Wayland research.

The new Desktop/applications entry is **Penguin LGHUB — Wayland test**, with
the project icon, startup feedback, setup check, help, logs and Quit actions.
In Ly, choose **Plasma (Wayland)**, then open that entry. The original
**Penguin LGHUB — CachyOS Test** entry remains the X11 fallback. KDE may request
input permission when a mapped key is first used; the launcher does not grant
permission automatically. No Logitech HID device was connected at preparation.

User acceptance is pending for this CachyOS Wayland installation: test the UI,
tray entries and graceful quit, then connected-device bindings, macros,
G-Shift, manual profiles and ordinary lighting. Native-Wayland automatic app
matching and screen-sampled lighting remain separate work. Passing the owner's
selected feature checks can close this scoped checkpoint without implying
that every feature is implemented or every Linux desktop is supported.

The existing Desktop validation note contains the test instructions. Machine
evidence is in [the Wayland receipt](cachyos-wayland-launcher-verification.json)
and `cachyos-wayland-startup-20261028-001/` beneath the validation directory.
