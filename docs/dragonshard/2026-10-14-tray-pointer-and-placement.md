# Chapter 19 — a visible tray menu was not yet a working input path

Cencyte confirmed that the color picker and peripheral-card hover repairs work.
His next real-desktop test found a tray menu in the wrong corner and a crash when
approaching it: the native InputStateManager called the missing
`GetPointerInputTransform` export. This investigation spans October 13–14, 2026;
a workstation failure and reboot interrupted the session. These dates are not a
measurement of active engineering time.

## Shipped repair and withdrawn approach

Patch `0036-pointer-input-transform-fallback.patch` supplies the export and reports
`ERROR_NO_DATA` without writing the output array when no transform is available.
The ordinary-mouse native caller then uses `ScreenToClient`. The negative-control
run with the old user32 reproduced the reported crash; the repaired build passes
19 API checks. Microsoft's [API contract](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getpointerinputtransform)
documents the absence-of-transform result.

The first positioning approach in commit bb9f07d altered the native executable's
layout calculations. Although geometry checks passed, full application screenshots
revealed a black 46-pixel-high menu. That approach was withdrawn. Geometry alone
was an insufficient acceptance test.

The replacement leaves the original SHA-pinned tray executable intact. The
existing dispatcher helper installs a thread-local CBT hook and subclasses only
`WinUIDesktopWin32WindowClass` in `lghub_system_tray.exe`, when
`GHUB_TRAY_ANCHOR=1`. It records the cursor on each opening and adjusts only the
host window's placement. It clamps to the selected monitor's work area, preserves
the opening anchor during later resizes, and releases subclass state on destruction.
Existing windows must also be enumerated when the dispatcher is created: merely
watching future window creation missed the native tray window.

The retired executable-overlay preparation/mount code is removed. The launcher
rejects obsolete `tray_executable` configurations instead of silently using them.

## Validation and deployment

- 19/19 pointer-transform API checks.
- 148/148 anchor geometry and window-lifecycle checks, including vertically
  stacked/negative monitor coordinates, work-area edges, resizing and reopening.
- 75/75 real native-menu checks on the final safe candidate: two opening
  positions, foreground ownership, full-sized window, repeated hover survival,
  outside-click dismissal and reopening. These **exclude menu actions**.
- Captured XComposite screenshots show the actual native menu text, not just a
  rectangle. During startup the first capture briefly lacks some row labels;
  later hover and reopened captures contain the full menu. Startup readiness and
  painting are not treated as equivalent to a window existing.
- All automated desktop interactions use owned Xvfb, private KWin/DBus, a separate
  network namespace, frozen application files and disabled hardware drivers.
  Correct test identity is uid 1000, gid **1001**. Earlier test runs using gid 1000
  were misleading and are not acceptance evidence.

Final safe evidence:
`wine-lab/codex-run/tray-pointer-20261013/final-safe-menu-20261014/`.
Unit evidence: `unit-host-anchor/` and the post-reboot `unit-final-20261014/`.

The existing Desktop installation at `wine-lab/codex-run/desktop-tested-20261012`
now has the new helper and `GHUB_TRAY_ANCHOR=1`, with **42 verified file pins**.
It retains the known-good user32, win32u and wineserver components; no experimental
mouse-to-pointer routing or native gesture DLL was enabled there. Files and config
were replaced atomically for the next launch. No live desktop input or process
termination was used. Rollback inputs are under
`tray-pointer-20261013/live-before-host-placement-20261014/`.

For the **two reported defects**, implementation is 2/2 and isolated validation
is 2/2. User-desktop acceptance of this replacement remains 0/2 reported. Those
numbers are not a percentage of complete tray functionality or complete G HUB
support. In particular, menu actions remain unresolved.

## What clicking the native menu revealed

The test intentionally goes beyond opening and hovering: its default mode clicks
“Close G HUB” and requires successful process exit. That full test still fails.
The `--placement-only` mode makes the narrower passing scope explicit in JSON and
stdout; it is not a way of declaring the action test successful.

The native InputStateManager consumes legacy mouse moves but ignores legacy
button messages. A private opt-in experiment promoted routed left-button events
to pointer messages and exposed their pointer/frame data. This reached deeper
native code and uncovered, in sequence:

1. `GetPointerDeviceRects` failed for the synthesized mouse device. Supplying the
   matching virtual-desktop geometry cleared that fail-fast.
2. `DwmShowContact` was absent. Backporting the newer Wine export cleared it;
   [upstream Wine](https://github.com/wine-mirror/wine/blob/master/dlls/dwmapi/dwmapi_main.c)
   still treats the visual contact feedback as a stub.
3. GestureRecognizer creation called `SetPropertyInteractionContext` with
   `INTERACTION_UI_FEEDBACK=FALSE`; Wine returned `E_NOTIMPL`. An experimental
   stored boolean cleared that error. This is the feedback
   [property documented by Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/interactioncontext/ne-interactioncontext-interaction_context_property).
4. Next, `GetInteractionConfigurationInteractionContext` was an unimplemented
   export. Wine's callback registration/configuration functions also lacked real
   packet processing, so returning success alone could not produce a working menu.
5. Reusing the user's native Windows `ninput.dll` avoided that immediate failure
   in one run, but did not produce the required Close behavior. A subsequent
   forwarding/instrumentation run failed on **user32 ordinal 2518**, before
   registration/packet trace output. Therefore the native DLL is not validated as
   a drop-in solution. The user's Windows user32 exports that ordinal unnamed at
   RVA `0x461d0`; its identity and required semantics remain to be established.
   A final read-only disassembly narrows this further: it caches whether helper
   `0x46388` returns a non-null value. That helper reads
   `\Registry\Machine\Software\Microsoft\Windows NT\CurrentVersion\Windows`
   and the value `NaturalInputHandler` (string RVAs `0xb2130` and `0xb2660`).
   This suggests an optional handler-availability predicate; it is an inference,
   not sufficient evidence to substitute a constant result or name the API.

The decisive source evidence is saved in the existing DragonShard batch1 corpus:
Microsoft.UI.Input.dll SHA
`942cf3f4b487eb1b512d359057968cfc0829468160b064793a04b476251ae29c`,
constructor helper `180028180`, configuration helper `1800256f0`, packet paths
`1800221a0`, `180022500`, `1800ad0d0`, `1800ad2d0`. The activation trace proves the
native GestureRecognizer factory is found; its construction subsequently fails
inside Wine's interaction context. This is not a missing WinRT class registration.

Evidence runs under `tray-pointer-20261013/` include `full-menu-gesture-trace/`,
`full-menu-feedback-property/`, `full-menu-native-ninput/`, and
`full-menu-gesture-packets-20261014b/`. Their failures are retained, not relabeled
as passing. `click-experiment/` preserves the exact experimental config and changed
binaries. The forwarding probe source is `ninput_trace.cpp`/`.def` in that lab;
`tray_menu_debug.cpp` captures native debug messages and raw stack candidates
(the latter are **not** an unwound backtrace).

## Resume without recreating the investigation

The experimental source differences are committed under `patches/wine/experiments/`:
`composition-mouse-pointer.patch`, `dwm-contact-feedback.patch`, and
`ninput-feedback-property.patch`. All three dry-run cleanly against the restored
production source. None is applied by the normal build scripts. The production
source and its user32/win32u/server/dwmapi/ninput build targets were restored and
rebuilt after archiving the experiment; candidate configuration was likewise
returned to the safe component set.

The pointer experiment needs more than another exported name before deployment:
message filtering must not overwrite the last accepted sample, history/lifetime
semantics need validation, drag/capture and enter/leave behavior are incomplete,
and only the left button is covered. It must remain off in the Desktop launcher.

Recommended next investigation: identify ordinal 2518 and compare the native
interaction engine's dependencies against the still-missing Wine packet-processing
APIs, then choose the smaller demonstrably correct path. Reusing the existing
engine could save substantial implementation scope, but the comparison has not
established that it is sufficient. Use a small activation/packet harness before
another full application run, then require real menu command delivery. Do not
restart a whole-corpus decompilation for these targeted functions.

Post-reboot verification: passwordless sudo and SystemSage work; Ghidra reconnects
with 197 tools and successfully reads Microsoft.UI.Input.dll's 6,252-function
project database. Both prepared installations passed pin checks. Root had about
53 GiB free, Captures about 442 GiB. No system packages, VMs or backup storage were
changed as part of this repair.
