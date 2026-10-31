# Bazzite readiness investigation — 2026-10-29

## October 31 follow-up

The initial inventory below is historical. The completed booted installer run
on October 30 recorded **Bazzite 44.20261026.0**, kernel **7.2.11**, KWin **6.7.5**,
Terra Xwayland **26.0.99.903**, libei **1.6.0**, PipeWire **1.6.10**, Podman **5.8.9**
and SELinux **Enforcing**. Installation and uninstall/reinstall were manually
completed, and the reported delayed/stuck G502 X Plus assignment was confirmed
fixed after patch 0046. Offscreen hardware EGL and PulseAudio connection probes
also passed. These confirmations supersede the original pending-install status;
they do not certify every G HUB feature or a full X11 desktop session.

Preview 4 contains installer 0.3.5 and its 31 GiB budget with workspace cleanup.
Fresh installation on CachyOS and the Manjaro VM was subsequently confirmed on October 31. See the
[current environment record](../../packaging/tested-environments.json) and
[installation guide](release-installer.md#verification-boundaries).

## Original investigation

Target inventory: Bazzite 44.20261012.0 KDE NVIDIA Open, Fedora 44, KWin 6.7.5,
kernel 7.2.9, NVIDIA 615.78.08, Terra Xwayland 26.0.99.903, libei 1.6.0,
Podman 5.8.7 and SELinux enforcing/targeted. These came from the installed image;
Penguin-LGHUB has not yet been run on that boot. No offline application install
or host package change was performed during this work.

## Full X11 sessions

This policy originates in Fedora KDE and is also followed by Bazzite. Fedora's
Plasma 6 transition dropped its supported X11 session to reduce maintenance,
with SimpleDRM and modern NVIDIA GBM support removing earlier fallback reasons.
It explicitly preserves Xwayland support for X11 applications.
[Fedora's change rationale](https://fedoraproject.org/wiki/Changes/KDE_Plasma_6).

There is a real workaround for compatible older images: independently maintained
`plasma-workspace-x11` supplies the session and pulls its dependencies. Fedora
44 lists a 6.7.5 build, but the Fedora KDE SIG does not support that package.
[Fedora package record](https://packages.fedoraproject.org/pkgs/plasma-workspace-x11/plasma-workspace-x11/).

Bazzite users have attempted `rpm-ostree install plasma-workspace-x11` followed
by reboot. The same community thread contains a later login-loop report and a
Bazzite maintainer's direction to remove that unsupported session before filing
support requests. This establishes a known workaround and discouragement of its
use, not a measured community-wide success rate.
[First-person reports and maintainer response](https://universal-blue.discourse.group/t/bazzite-3-0-no-etc-default-desktop-wayland-to-enable-x11/1413).

Plasma 6.8, released October 14, 2026, retires the upstream X11 session while
retaining Xwayland and X11 application compatibility. The installed 6.7.5 image
predates that change; future image updates make layering the legacy session an
increasingly poor release prerequisite.
[KDE Plasma 6.8 announcement](https://kde.org/announcements/plasma/6/6.8.0/).

Recommendation: qualify Bazzite's normal KDE Wayland session and its Xwayland
application path. A decision on retaining full X11 as a separate
provenance requirement is pending. The installer therefore makes no claim of a second
Bazzite X11 result and does not install that session.

## Installation and host boundaries

| Area | Implemented accommodation | Remaining target evidence |
|---|---|---|
| Atomic OS | Private payload under the user data directory; only scoped rules in writable `/etc`. No host Wine, compiler, Docker, dnf or rpm-ostree transaction. | Booted prerequisite/namespace checks. |
| Acquisition | Pinned private SDK and Wine; bubblewrap default, optional rootless Podman. Both engines reproduced 2/2 DLL hashes locally. | Podman SELinux execution on Bazzite, if chosen. |
| Graphics | Existing pressure-vessel host-driver import; added offscreen EGL context/vendor/renderer report. Local NVIDIA hardware context passed. | NVIDIA 615 and Fedora `/usr/lib64` imports, compositor behavior. |
| Audio | Existing session-socket import; added a no-autospawn PulseAudio connection probe, with no stream or sound. Local PipeWire-pulse passed. | Bazzite service/socket policy and any desired G HUB audio features. |
| Devices | Logitech-only USB and hidraw uaccess rules before seat-late; preserve logind ACLs. Inspect actual nodes/access. | Reconnect, receiver enumeration, input bindings and permissions. |
| Tray/synchronization | Preserve per-application NTSYNC masking and Wine server synchronization from CachyOS. Native tray/XEmbed test passes. | Plasma tray proxy/menu/input in the Bazzite session. |
| SELinux | Preserve enforcing mode; record namespace errors. Restore device-rule labels; optional Podman labels only private acquisition trees. | Actual process labels and any AVC denials. |
| Wayland | KDE portal/Xwayland/libei checks, interactive input authorization. | Real bindings in a native Wayland application. |

Bazzite recommends limiting package layering; the user-data installation avoids
turning build dependencies into layered system packages.
[Bazzite package-layering guidance](https://docs.bazzite.gg/Installing_and_Managing_Software/rpm-ostree/).

The app uses pressure-vessel/bubblewrap directly. Distrobox is installed in the
image but is not used by Penguin-LGHUB. Valve's runtime supplies the userspace
and imports host graphics; it cannot replace a kernel driver or prove compositor
behavior merely by resolving libraries.
[Valve runtime diagnostics](https://github.com/ValveSoftware/steam-runtime/blob/master/doc/reporting-steamlinuxruntime-bugs.md).

Stock Bazzite already has a Logitech hidraw uaccess rule. The project rule also covers
USB-device nodes, with no world-write permissions or broad input-group change.
The active local seat matters; reconnect after installation if existing device
ACLs have not been refreshed. Setup reloads rules without retriggering unrelated
devices. Existing input-remapper services are not disabled speculatively.
[udev rule and `/etc` precedence documentation](https://www.freedesktop.org/software/systemd/man/latest/udev.html).

SELinux's Podman container policy is distinct from the process domain of a
direct user-launched pressure-vessel process. Do not assume that a generic
container-device boolean fixes this application. Rootless Podman's `--rootfs`
requires appropriate private-tree labels; `:Z` is limited to its work/source
copy. No whole-home or system relabel is part of setup.
[Podman run documentation](https://docs.podman.io/en/latest/markdown/podman-run.1.html).

If the Bazzite run fails, retain the printed installer log and host JSON; inspect
`getenforce`, `id -Z`, `ls -Z` for the affected private paths and
`sudo ausearch -m AVC,USER_AVC -ts recent`. Diagnose the denied operation before
considering a narrowly scoped policy change. Do not switch enforcing off or
automatically generate a broad allow policy.

The investigation covers all eight areas in the table (8/8 research coverage).
At the time of this investigation, the booted installation test was pending.
The October 31 follow-up above records the subsequent successful installation
and targeted physical-input confirmation. Local loader/UI/probe success is separate evidence,
not a percentage estimate of Bazzite compatibility.
