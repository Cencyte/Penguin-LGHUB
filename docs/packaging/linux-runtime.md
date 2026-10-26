# Pinned Linux userspace: first executable portability check

Checked October 26, 2026. This is a development experiment, not a qualified
G HUB application container or a release installer. The working Desktop
launcher remains unchanged.

## Decision and measured result

Use a complete private Wine inside a versioned **Steam Linux Runtime 4**,
launched through its pressure-vessel tooling. Obtain the runtime directly
from Valve; Steam and a Steam account are not required. This stabilizes Linux
userspace without replacing the host's Wine, libc or packages.

The [lock](../../packaging/linux-runtime.lock.json) pins Valve build
`4.0.20260928.262390` (glibc 2.41), pressure-vessel `0.20260925.0`, and Debian
`libunwind8` version `1.8.1-0.1`. Downloads are checked against recorded
SHA-256 values from the publishers' HTTPS checksum pages. This is not a claim
that detached package signatures were verified. Original package notices are
retained; upstream source-package locations are recorded. No downloaded
binary is committed to this repository.

| Check | Result | Scope |
|---|---:|---|
| Native ELF modules inspected | 46/46 | Entire assembled private Wine ELF inventory |
| Modules with resolved direct/transitive dependencies | 39/46, 84.8% | No host graphics-provider import |
| Candidate dynamic libraries successfully loaded | 38/54, 70.4% | Names extracted from Wine binaries; candidates are not necessarily mandatory |
| Real Windows command executed | Pass | New disposable prefix; `cmd /c echo PENGUIN_LINUX_RUNTIME_OK`, exit 0 |
| Containerized G HUB API/GUI/device qualification | Not performed | Previous host-userspace backend success does not count here |
| Fresh Manjaro/CachyOS installation qualification | 0/2 | Unchanged by this container test |

The unmodified Valve platform resolved only 10/46 modules. All 36 failures
included `libunwind.so.8`. Adding the 56,252-byte official Debian package
privately increased the result to 39/46. Nothing was installed with pacman,
apt or sudo, and the user's existing Steam runtime was not modified.

The PE command succeeded, but Wine prefix initialization also emitted a
libdbus assertion about an already-set error object. That diagnostic is
retained in [the sanitized full report](linux-runtime-verification.json).
Treat it as an outstanding startup investigation, not a silent pass for
every service started during Wine initialization.

## Exact remaining native-module gaps

| Wine module | Missing libraries in this runtime | Next action |
|---|---|---|
| `winedmo.so` | `libavcodec.so.62`, `libavformat.so.62`, `libavutil.so.60` | Build against an explicitly pinned compatible FFmpeg stack inside the SDK; preserve required multimedia behavior. Do not alias incompatible sonames. |
| `winegstreamer.so` | GStreamer core, base, audio, tag and video `1.0` libraries | Add a pinned runtime-compatible GStreamer library/plugin closure and retain its licenses. |
| `gphoto2.so` | `libgphoto2.so.6`, `libgphoto2_port.so.12` | Pin the camera-library dependency closure if retaining this Wine module. |
| `opencl.so` | `libOpenCL.so.1` | Supply the ICD loader, then qualify actual host vendor ICD integration separately. |
| `sane.so` | `libsane.so.1` | Pin scanner-library closure if retaining this Wine module. |
| `winewayland.so` | `libxkbregistry.so.0` | Supply a compatible library; this alone does not implement global key output on native Wayland. |
| `wpcap.so` | `libpcap.so.1` | Rebuild against the SDK's declared libpcap ABI or provide the exact compatible library. |

The dynamic-library candidate check additionally reports `libnetapi.so.1`
and `libodbc.so.2`. The report includes every result and loader diagnostic.
No module was deleted, replaced with a fake success, or declared unnecessary
just to improve the percentage.

## Reproduce without touching the live session

Prerequisites: Python 3.12+, working unprivileged bubblewrap namespaces,
x86-64 Linux, and the previously assembled verified Wine bundle. The
development host's package set remains pinned. These commands do not install
or upgrade system packages. Keep the runtime, cache and outputs outside the
source checkout; use a new output directory for each prepare/audit.

```sh
mkdir -p "$HOME/.local/state/penguin-lghub" "$HOME/.cache/penguin-lghub/linux"
python3 tools/packaging/linux_runtime.py fetch \
  --cache "$HOME/.cache/penguin-lghub/linux"
python3 tools/packaging/linux_runtime.py prepare \
  --cache "$HOME/.cache/penguin-lghub/linux" \
  --output "$HOME/.local/state/penguin-lghub/linux-runtime"
python3 tools/packaging/linux_runtime.py audit \
  --linux-runtime "$HOME/.local/state/penguin-lghub/linux-runtime" \
  --wine /absolute/path/to/verified/private-wine \
  --output "$HOME/.local/state/penguin-lghub/linux-audit-001"
```

The audit deliberately exits **1** while any ELF dependency remains
unresolved or the Windows command fails. Read `linux-audit.json` for the
result and `linux-audit.log` for namespace/launcher diagnostics. Exit 1 with
a completed report is not a failed installation of the host system.

The outer bubblewrap environment has a private network and PID namespace,
synthetic `/dev`, private `/tmp`, and hidden live user-session sockets and
system Wine. The inner pressure-vessel uses a private home, imports no host
graphics stack and selects the pinned platform. Only the new audit output
and private runtime scratch directory are writable. Checks preserve a
20 GiB free-space floor. Wine creates a disposable test prefix, then only
that prefix's server is stopped. No physical mouse/keyboard input is sent.

## What comes next

1. Pin the matching SDK/toolchain and rebuild the full custom Wine in it.
   Close the seven known module gaps with an explicit dependency manifest;
   keep the current runtime as the regression baseline. A build in the
   target userspace is preferable to accumulating arbitrary host libraries.
2. Extend the private-container test to the eight WinRT activation factories
   and five existing G HUB backend API routes. Package its helper runtime
   explicitly, including the Node/WebSocket requirement. Resolve the D-Bus
   startup diagnostic before treating initialization as clean.
3. Add a separate real-session launcher that deliberately connects X11,
   tray IPC, audio if needed, and scoped USB/HID devices. Pressure-vessel
   should import host graphics drivers for this path. Do not replace kernel
   driver libraries with unrelated bundled ones or use blanket privileged
   access. Device hotplug permissions remain a host responsibility.
4. Qualify a fresh Manjaro installation, then CachyOS with the actual
   hardware/GPU. Include key bindings, macros, G-Shift, lighting, profile
   persistence, tray actions and rendering regressions. Debian follows;
   Bazzite remains unverified because XTEST does not solve native Wayland
   global input.

These are separate acceptance stages, not four equally weighted quarters
of the work. The dependency audit covers 100% of the currently packaged ELF
inventory; application behavior in this new userspace remains untested.

## Upstream references

- [Valve's runtime and SDK guidance](https://github.com/ValveSoftware/steam-runtime)
- [Pinned official runtime publication](https://repo.steampowered.com/steamrt4/images/4.0.20260928.262390/)
- [Debian libunwind package checksum](https://packages.debian.org/trixie/amd64/libunwind8/download)
