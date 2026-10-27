# Pinned Linux userspace: native libraries and G HUB backend verified

Checked October 26, 2026. Current scope: **Manjaro and CachyOS on X11**.
Bazzite/Wayland work is deferred; Debian is a later host target. These checks
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
20 GiB free-space floor. SDK preparation reserves 7 GiB beyond that floor;
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

1. **Integrate a real-session container launcher** with explicit X11/tray
   connections, host graphics-driver import, the existing input bridge and
   narrowly scoped HID/USB access and hotplug. The current tools intentionally
   expose none of those. A headless backend pass is not a working desktop
   container. Do not replace the known-working launcher until this candidate
   passes user-driven tests.
2. **Finish the complete corresponding-source/build pipeline**, dependency
   acquisition policy, font/UI qualification and installer/uninstaller
   integration. No ready-to-install binary release is supplied yet.
3. **Qualify a fresh Manjaro control, then CachyOS/X11** on actual hardware.
   Cover installation/relaunch, device discovery and reconnect, native Linux
   key output, macros, G-Shift press/release, profiles, lighting, tray actions,
   resize/hover/color interactions, update freeze and rollback. Graphics and
   device checks remain available for the user to perform without a deadline.

Debian host testing follows later. Bazzite and native Wayland need a separate
plan; Distrobox alone does not translate XTEST global input into native
Wayland input. No Bazzite implementation or validation is included here.

## Upstream references

- [Valve runtime and SDK guidance](https://github.com/ValveSoftware/steam-runtime)
- [Pinned official runtime/SDK publication](https://repo.steampowered.com/steamrt4/images/4.0.20260928.262390/)
- [Official Node 22.21.0 checksums](https://nodejs.org/dist/v22.21.0/SHASUMS256.txt)
- [Debian package archive](https://deb.debian.org/debian/)
