# Packaged Wine keyboard output reaches native Wayland

> Historical investigation record. For current installation, X11/Wayland profiles and validation status, see the [installation guide](release-installer.md) and [recorded environments](../../packaging/tested-environments.json).

**October 30 follow-up:** the single-thread fixture below missed a cross-thread
key-release defect. Patch 0046 now keeps G HUB's host output on one connection.
The [threaded regression receipt](host-keyboard-threads-verification.json)
records before/after delivery, repeats, modifiers, worker exit and hold checks.
The historical results below remain scoped to their original fixture.

The threaded follow-up can be reproduced with existing developer tools:

```sh
g++ -fPIC tools/packaging/wayland_input_receiver.cpp -o /path/to/receiver \
  $(pkg-config --cflags --libs Qt6Widgets)
x86_64-w64-mingw32-gcc -O2 -static tools/packaging/host_keyboard_threads.c \
  -o /path/to/thread-probe.exe
python3 tools/packaging/host_keyboard_threads.py \
  --output /path/to/new-disposable-output --wine /path/to/private-wine \
  --receiver /path/to/receiver --sender /path/to/thread-probe.exe \
  --permission test-app
```

Use `test-direct` for the older KWin 6.5 test compositor. `--target xcb` selects
an Xwayland receiver; the default is native Wayland. `--os-root` can use an
offline Linux deployment's userspace, read-only. `--module` tests a replacement
`winex11.so` through a read-only namespace mount without modifying the runtime.
The seven scenarios check 74 non-repeat transitions, with a 150 ms delivery
deadline, plus permitted repeats during an intentional hold. Wine and the
receiver use the same boot-time clock, including suspend time. Cold-start
authorization, real USB input, G HUB macros and the complete launcher are
outside this fixture. No application or input is sent to the live desktop.

October 27, 2026. Follow-up to the [Wayland investigation](2026-10-27-wayland-research.md).
The next implementation experiment succeeds on **Manjaro first, then CachyOS**:
our existing patched Wine 10.20, inside the pinned Linux runtime, delivers the
22-event Windows keyboard fixture to a native Wayland receiver through the
host's Xwayland/KWin input integration. No new Wine patch or privileged input
broker was needed for this tested path.

This is transport validation, not a claim that the complete G HUB installer
supports Wayland. The actual Logitech application and peripherals are absent
from these experiments. The production launcher continues to require X11.

## Results and coverage

| Test | Manjaro VM, KWin 6.5.6 | CachyOS physical host, KWin 6.7.5 |
| --- | --- | --- |
| Packaged Wine, bridge enabled, disposable preauthorization | **22/22 exact native Wayland events** | **22/22 exact native Wayland events** |
| Same executable with bridge disabled | **0 events**, expected | **0 events**, expected |
| Same fixture under a different executable name | **0 events**, expected | **0 events**, expected |
| Normal permissions, no approval | RemoteDesktop `CreateSession`, `SelectDevices`, backend `Start`; no `ConnectToEIS`; **0 events** | `kwin_eis_prompter --client lghub_agent.exe`; **0 events** |

Measured: **6/6 routing cases passed (100%)**, plus **2/2 pending-permission
controls passed (100%)**. Full G HUB Wayland acceptance remains **0/2 target
systems (0%)**. The physical CachyOS host ran a virtual-output compositor with
software rendering; this does not qualify its physical GPU or input devices.

The positive sequence covers brackets, zero, extended Delete, right-Control+A,
extended Left, a scan-code-only A, main Enter, keypad Enter and F12. The native
receiver checks order, symbol, native scan code, modifier state and repeat
state. Both Enter variants remain distinguishable, Control ends released, and
there are no extra events in the observed stream. Other layouts, held keys,
focus changes, mouse/Unicode macros and disconnect handling remain untested.

All three SendInput fixtures return success even in the pending-permission
case. A nonzero `GetLastError` printed after a successful SendInput is stale
diagnostic state, not an API failure. Receiver delivery is the acceptance
criterion; successful submission alone must never become a launcher claim
that bindings work.

Evidence: [machine-readable results and provenance](wayland-packaged-wine-verification.json).

## Isolation and reproducibility

`tools/packaging/wayland_wine_probe.py` reuses the installer's complete Wine and
Linux-runtime inventory/hash checks. It creates a fresh prefix, private KWin
with virtual output, native Qt Wayland receiver, private bus, network, PID,
`/tmp`, `/run` and synthetic `/dev`. The inner pressure-vessel container shares
the test compositor's PID namespace, as the desktop launcher does with its
host. The existing server-synchronization policy is preserved: no `/dev/ntsync`
exists inside the test.

Manjaro's positive test uses `XwaylandEisNoPrompt=true` **only in its disposable
configuration**. This deliberately bypasses consent to isolate transport; it
is not an installer policy. CachyOS's positive test allows only the test client
name in that disposable configuration. Separate runs leave permissions unset.
No live desktop configuration, session, prefix, input focus or device permission
was changed. No packages were installed or upgraded.

The Windows fixture is our existing `tools/wine/host_keyboard_probe.c`, built
with the already installed MinGW compiler and copied as `lghub_agent.exe` to
exercise the agent-only patch. It is **not Logitech's agent**. The native receiver
was built with the workstation's existing Qt 6.10.1 development files and
linked successfully against Manjaro Qt 6.10.2 and CachyOS Qt 6.11.2. Fixture
source and binary hashes accompany the evidence. Output directories preserve
logs, commands, harness snapshot, package versions and fresh prefix.

Build the developer fixtures without installing dependencies:

```sh
g++ -fPIC tools/packaging/wayland_input_receiver.cpp -o /path/to/receiver \
  $(pkg-config --cflags --libs Qt6Widgets)
x86_64-w64-mingw32-gcc -O2 -Wall tools/wine/host_keyboard_probe.c \
  -o /path/to/host_keyboard_probe.exe -luser32

python3 tools/packaging/wayland_wine_probe.py \
  --output /path/to/new-disposable-output \
  --wine /path/to/verified-private-wine --linux /path/to/verified-linux-runtime \
  --receiver /path/to/receiver --sender /path/to/host_keyboard_probe.exe \
  --permission test-direct
```

Use `test-direct` and `portal` for the measured Manjaro 6.5 stack; use `test-app`
and `test-pending-app` for the measured CachyOS 6.7 stack. These are test modes,
not options for granting permissions to a user's desktop. Prebuilt fixtures
avoid adding a compiler to stock test hosts. Each run requires a new output
directory. Nothing downloads or uploads proprietary application files.

## What the failed bring-up runs taught us

1. The Linux-runtime root must be explicitly exposed inside pressure-vessel,
   as the real desktop launcher already does. Without that mount, supplemental
   `libunwind.so.8` is unavailable and Wine's loader emits a misleading final
   `ntdll.so` path error. This was a harness omission, not a missing packaged DLL.
2. Initialize the fresh test prefix without a display and with Mono/HTML
   bootstrap disabled, then restart only its private wineserver before using
   Xwayland. First-run dialogs otherwise prevent unattended initialization.
3. The private KDE session needs `XDG_MENU_PREFIX=plasma-` and its application
   cache built with `kbuildsycoca6`. Without the cache, KWin cannot associate the
   portal backend with its installed desktop entry and withholds the restricted
   screencast protocol. Creating sessions then fails before consent is reached.
   The successful run recognizes the existing desktop entry's permissions;
   it does not disable KWin's interface permission checks. A private PipeWire
   instance supplies the test session's media service.

The third finding follows KWin's [desktop-entry lookup](https://github.com/KDE/kwin/blob/v6.5.6/src/utils/serviceutils.h)
and [interface filter](https://github.com/KDE/kwin/blob/v6.5.6/src/wayland_server.cpp).
KDE's [RemoteDesktop backend](https://github.com/KDE/xdg-desktop-portal-kde/blob/v6.5.6/src/remotedesktop.cpp)
requires that interface even for this input request. The failed attempts are
retained as harness diagnostics; they are not counted as application failures
or successful permission tests.

## Installer implications and next implementation

The next implementation target is an **explicit opt-in KDE/Xwayland launch
mode**, initially on the Manjaro VM. Preserve the pinned runtime, X11 Wine
driver, update freeze, application integrity, HID access policy, tray setup and
server synchronization. Add session/backend diagnostics and permission guidance
before enabling broader use; the current X11 default remains available.

Manjaro's measured older portal sends an empty application ID and device mask
3 (keyboard plus pointer). Do not promise an application-named approval dialog
or advise globally granting all unnamed clients. CachyOS's newer KWin identifies
this packaged test process as `lghub_agent.exe`; verify the same name with the
actual agent before writing final user instructions. No approval, denial,
revocation or persistence click was performed in this pass.

Then test the actual G HUB UI and tray under Manjaro Wayland, followed by
user-driven hardware tests and the same sequence on CachyOS. Include a Wine
target as well as a native Wayland target, permission acceptance/revocation,
key releases across focus/lock/restart, every tray action and device reconnect.
Automatic native-app profile matching and screen-sampled lighting still have
separate compositor/capture requirements. Passing this keyboard experiment
does not settle those features.
