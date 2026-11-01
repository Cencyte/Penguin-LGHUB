# Opt-in Manjaro Wayland launcher

> Historical investigation record. For current installation, X11/Wayland profiles and validation status, see the [installation guide](release-installer.md) and [recorded environments](../../packaging/tested-environments.json).

**Follow-up:** the initial smoke test missed deletion of the extracted depots
in the prefix later handed to the user. Its window/tray result did not establish
frontend readiness. See [the depot-state repair](2026-10-27-wayland-depot-repair.md)
for the corrected recipe, separate user installation and stronger restart test.

October 27, 2026. The Manjaro VM now has a separate **Penguin LGHUB — Wayland
test** launcher. It uses the existing verified Wine 10.20 and Linux runtime,
with a separate prepared application prefix. The existing Startup fix/X11
shortcut and installation remain available.

## What changed

`desktop_runtime.py --experimental-wayland` requires a local KDE Plasma
Wayland session. Before starting Wine it checks the owned Wayland socket,
authenticated local Xwayland connection, XTEST extension, KWin session bus
owner, and RemoteDesktop portal with ConnectToEIS. These are capability checks,
not a claim that the user has granted input permission.

The worker explicitly selects Wine's patched X11 driver in this prefix and
uses Xwayland. Native Wine Wayland support is not being substituted for the
tested composition/input patches. The packaged DLL checks, frontend fixes,
font replacements, device policy, update freeze, graphics-provider import,
server synchronization and session supervision follow the established launcher.
No KWin allowlist, global input-permission setting or system Wine installation
is changed. The default launch mode still requires X11.

The new shortcut carries the project icon and four user actions: **Check
setup**, **Wayland test help**, **Open test logs**, and **Quit this G HUB
session**. Launch errors use kdialog, with an xmessage fallback. No automatic
input recording or user-test deadline is enabled.

## Installed locations on the Manjaro VM

| Item | Location |
| --- | --- |
| Desktop shortcut | `/home/firesongz/Desktop/Penguin-LGHUB Wayland Test.desktop` |
| User instructions | `/home/firesongz/Desktop/Penguin-LGHUB Wayland Test.md` |
| Launcher source | `/home/firesongz/Source2/Penguin-LGHUB-wayland-launcher-20261027` |
| Separate installation | `/home/firesongz/.local/state/penguin-lghub-validation/wayland-desktop-001` |
| Logs | Installation directory's `logs/` |
| Previous X11 installation | `/home/firesongz/.local/state/penguin-lghub-validation/desktop-003` |

The new prefix was prepared from verified `candidate-003`, including the
frontend transformation, instead of copying an uncontrolled live prefix.
Runtime files are shared; application settings in the two prefixes are separate.

The user explicitly requested **kdialog** and then **xwininfo**. Installed
`kdialog 25.12.3-1` and `xorg-xwininfo 1.1.6-1` from signed Arch archive packages,
after checking installed-library linkage and package transaction previews.
Both are pinned in `/etc/pacman.conf`; previous pin settings are preserved and
configuration backups are retained. Only these two packages were added: no
dependencies or other packages were upgraded. xwininfo is a test diagnostic,
not a requirement of the launcher itself.

## Verification and limits

**96/96 packaging unit tests pass (100% of the current suite)**, including
14 launcher tests. These cover explicit opt-in, session restrictions, real
socket validation, preservation of X11 defaults, environment filtering and
shortcut actions, alongside the existing packaging and compatibility checks.

An isolated full-launcher smoke test runs the production command under a
private KWin virtual output and Xwayland, private session bus and portals,
private network/PID namespaces, and a synthetic `/dev` without host input
devices. A small XEmbed tray host receives the actual G HUB tray's dock request.
The production launcher starts the real agent/frontend, and its normal Quit
action stops its owned session. The test does not grant input permissions or
send input. The live X11 session is left untouched.

The first run reached agent readiness and tray registration but its final
diagnostic failed because xwininfo was absent. The second run used installed
xprop and passed; after the requested xwininfo installation, the final run also
records the private window tree. Results and source hashes are saved in
`manjaro-wayland-launcher-verification.json`. Full logs and the executed harness
are retained in the VM's `wayland-launcher-smoke-*` directories next to the
installation. These observations establish startup/cleanup and tray docking;
they do not establish rendering quality or clickable menu behavior.

**Actual Wayland desktop/hardware acceptance remains 0/2 target systems**
(Manjaro and CachyOS). The previous keyboard transport experiments established
the input route under controlled conditions; this pass makes the actual G HUB
application available for the owner's normal desktop test. Physical GPU,
panel presentation, all tray actions, key permission approval/revocation,
device reconnect and hardware feature behavior remain user-test items.
Automatic profile matching for native Wayland applications and screen-sampled
lighting require separate work.

## User test

1. Quit any running G HUB session, log out, then select **Plasma (Wayland)**.
2. Open **Penguin LGHUB — Wayland test** on the VM Desktop. An empty device list
   is expected until a physical Logitech peripheral is passed through to the VM.
3. Try window minimize/restore, tray appearance and menu actions. With a device
   attached, try bindings in a native Wayland application, macros, G-Shift,
   manual profiles and ordinary lighting. On the first assignment, KDE may
   request input control. Manjaro's older portal may show an unnamed X11
   application requesting keyboard and pointer access; approve only the request
   you triggered for this test. The launcher does not approve it automatically.
4. Use the shortcut's right-click actions for help, diagnostics, logs or Quit.
   Return to Plasma (X11) and the existing launcher whenever desired.

To reproduce shortcut creation for an already prepared private installation:

```sh
python3 tools/packaging/desktop_runtime.py \
  --config /path/to/separate-installation/desktop.json \
  --experimental-wayland install-entry \
  --destination "$HOME/Desktop/Penguin-LGHUB Wayland Test.desktop"
```

This does not install an entire fresh system or grant device access. Preparation
and the host prerequisites still use the existing packaging workflow.
