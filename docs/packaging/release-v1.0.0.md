# Penguin-LGHUB v1.0.0

The v1.0 release completes the recorded manual acceptance on the three
initial target systems. It promotes the Preview 8 implementation with installer
**0.3.10**; the release changes project-version metadata and documentation without
introducing new installer or runtime behavior after those tests.

| Test target | Accepted desktop sessions |
|---|---|
| Manjaro VM | X11 and KDE Plasma Wayland |
| CachyOS bare metal | X11 and KDE Plasma Wayland |
| Bazzite bare metal | KDE Plasma Wayland |

Final acceptance was reported November 1, 2026. The requested checklist covered
uninstall/reinstall, automatic Kate profile switching, key bindings with clean
key release and macros where hardware is available, tray actions, edge resizing,
snapping, and minimize/restore. The VM covers UI/session behavior; physical
device testing comes from bare metal. The original Manjaro workstation also has
separate development-installation evidence.

The release includes:

- A private, source-built Wine 10.20 runtime and pinned Linux userspace, with
  G HUB 2026.6.974819 acquired from verified vendor depots.
- One desktop launcher that selects X11 or KDE Plasma Wayland for each login
  session, with automatic native-application profile observers.
- Shared keyboard output for reliable key press/release, native tray integration,
  and the accepted window restore, resize and bottom-edge corrections.
- A default-install entry point, compatibility checks, persistent diagnostics,
  transaction rollback and receipt-scoped uninstall.

The combined suite contains **264 project regression tests**. The preceding
resize validation recorded 36 matching native-size/bottom-edge checks and 24
minimize/restore cycles in isolated X11/Wayland sessions. Manual target acceptance
is recorded separately from automated measurements in the
[validation record](release-installer-verification.json). Package inventories and
graphics details are in the [environment record](../../packaging/tested-environments.json).

Download **Penguin-LGHUB-v1.0.0-source.zip** from the
[release](https://github.com/Cencyte/Penguin-LGHUB/releases/tag/v1.0.0), extract it,
and run `./install.sh` from that folder. If extraction loses executable
permissions, use `bash install.sh`. Reserve **31 GiB** on native Linux storage;
setup requests sudo for its scoped device-access rule. See the
[installation guide](release-installer.md) for prerequisites and options.

An existing installation is preserved. Back up any profiles/settings before
using `./install.sh --uninstall` and reinstalling: removal deletes those private
settings. An accepted Preview 8 installation already has this implementation;
reinstallation solely to obtain the v1.0 label is unnecessary.

This remains experimental software for the recorded configurations. Full Bazzite
X11 sessions, other distributions, non-KWin Wayland compositors, Flatpak profile
identities and screen-sampled lighting are outside the qualified scope. The
earlier VM agent crash report is retained; no recurrence was reported in final
acceptance, and no separate crash fix is claimed.

The ZIP contains project source and recipes, with no proprietary Windows/G HUB
payloads, populated prefixes or compiled Wine runtime. `SHA256SUMS` verifies the
attached ZIP; the file manifest covers its source contents. The existing
[third-party use boundaries](third-party-use.md) remain unchanged. Repository
visibility is managed separately from release tagging.
