# Release-candidate installer

The `release` branch provides the complete source-to-launcher entry point.
It downloads pinned inputs, builds the changed Wine components and creates a
fresh application prefix. No pre-existing lab build, donor prefix, host Wine,
Docker daemon or host MinGW compiler is an input to this workflow.

Boot the target OS and run these commands in its normal KDE desktop terminal:

```sh
git clone --branch release https://github.com/Cencyte/Penguin-LGHUB.git
cd Penguin-LGHUB
./install.sh --install
```

The repository currently requires GitHub access while it remains private.
Run as your regular user. The installer requests sudo for its small Logitech
device-rule helper; it never starts G HUB as root. It installs a launcher for
the current session in the resolved Desktop and application-menu directories,
with the project icon and Quit/check/log actions. For the other session on an
OS that offers both, use `desktop_runtime.py install-entry` with the installed
`desktop/desktop.json` configuration and `--experimental-wayland` for Wayland.
Bazzite's intended test is its default Plasma Wayland session; setup does not
add an unsupported full-X11 desktop.

Initial scope: x86-64 Manjaro, CachyOS and Bazzite KDE. Reserve **80 GiB free**
for the first build, downloaded media, extracted depots, verification copies and
20 GiB of retained headroom. This is a working-space requirement, not a download
size. The build reuses verified downloads on retry. Use
`--directory /path/to/spacious/local/storage` if necessary. Keep that directory
after installation: it contains the actual runtime and desktop prefix.

Host prerequisites are Python 3.12+, bubblewrap, zstd, sudo, udev, XDG user
directories, gdbus and kdialog, with authenticated local X11/Xwayland access;
Wayland checks also use xdpyinfo and the KDE remote-desktop portal. These are
checked before acquisition. No host package transaction or system upgrade is
performed. Missing prerequisites produce a named error rather than silently
changing an immutable image or a version-pinned workstation. OpenSSL libcrypto
is used for the encrypted Logitech resource depots.

`./install.sh --check` records host capabilities without installing the payload.
`./install.sh --help` follows the project's USAGE/SUMMARY/FLAGS/EXAMPLES format.
No arguments prints only usage. `--verbose` displays command output beneath
each colored step; output is always retained in a mode-0600 log under
`${XDG_STATE_HOME:-~/.local/state}/penguin-lghub/`. Host inventory and step-specific
probe logs are retained too. Do not publish raw logs without checking them for
local paths, usernames, device identifiers and application state.

The logo is pre-rendered by Chafa into verified kitty, sixel and symbol streams.
Thus it is the first installer display without installing Chafa on the host.
Kitty/sixel artwork is 200×200 pixels; the 20×10-cell symbol fallback approximates
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
`--engine podman` uses existing rootless Podman for that acquisition step;
the source build and application runtime still use bubblewrap/pressure-vessel.
Podman needs working rootless user-namespace mappings. On SELinux hosts, only
the dedicated private toolchain/work directories are labelled for container
access. There is no Docker requirement, privileged container, host-home relabel,
or SELinux-disable step. **Distrobox is not part of this implementation.**

## Retry and verification boundaries

Re-run the same command from the same source revision after a failure. Completed
steps are recorded, and integrity checks precede application preparation/use.
Interrupted extraction/preparation directories are renamed `.incomplete-N`
and preserved before retry; Wine's source compilation resumes incrementally.
Changed source revisions require a new installation directory. Setup does not
silently overwrite an existing installation or remove preserved diagnostics.

The build and isolated UI/tray checks passed on the development host; rootless
Podman and bubblewrap independently reproduced both Windows DLL hashes.
See [the measured validation record](release-installer-verification.json).
The assembled top-level workflow passed all 16 payload/verification stages in a
fresh private directory, using reverified download caches. Host-rule installation
and live desktop promotion were deliberately excluded; both launcher variants
were generated separately in a private directory. A complete
unassisted clone-to-device installation on Bazzite is **still the next user
acceptance test**, not a result inferred from these checks.

After booting Bazzite, the owner will run the public workflow and test device
discovery/reconnect, key bindings in a native Wayland app, macros, G-Shift,
profile changes, ordinary lighting, tray actions and relaunch. KDE may request
input-control authorization when an assignment is used. Setup does not approve
that permission. Automatic profile detection of native Wayland applications and
screen-sampled lighting remain separately scoped limitations.

The [Bazzite readiness findings](bazzite-readiness.md) explain the remaining
host-policy/driver boundaries. Existing [third-party use questions](third-party-use.md)
are unchanged by local extraction or containerization; the repository contains
no Microsoft or Logitech payload binaries.
