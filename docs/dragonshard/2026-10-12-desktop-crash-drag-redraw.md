# Chapter 14: three defects found through actual desktop testing

The user reported a tray crash in `RecordFeatureUsage`, a frameless main window
that kept following the pointer after button release, and incomplete drawing
that changed when the window moved. Earlier headless tray survival had not
established desktop acceptance. This pass reproduces the failures and repairs
three separate paths without changing the installed host Wine packages.

## Missing feature-usage export

The supplied stack enters Wine's delay-load failure hook from native
`Microsoft.DirectManipulation.dll`. The API set already maps to Shcore in Wine
10.20, but Shcore does not export `RecordFeatureUsage`.
[Microsoft's declaration](https://learn.microsoft.com/en-us/windows/win32/api/featurestagingapi/nf-featurestagingapi-recordfeatureusage)
specifies a void function taking three UINT32 values and a PCSTR. Patch 0032
accepts this infrastructure telemetry report without changing feature state or
starting a reporting service. It is not a complete Windows telemetry service.

The regression checks API-set/Shcore entry-point identity, invokes a delay
import, covers boundary arguments, and issues 4,000 reports from four threads.
The old DLL fails with missing export/error 127; the patched DLL passes.

## A move request after the button was released

A disposable copy of G HUB's actual Electron 39.1.2 runtime loads a small
frameless fixture. All XTEST input targets an owned Xvfb display. KWin runs on a
private bus with no desktop-service activation directories.

The initial diagnostic incorrectly counted an unchanged window as a successful
release even though no drag began. The corrected regression requires measurable
movement before checking release. Small movements inside the caption reproduce
the user's failure, matching Chromium's deferred caption-move behavior in its
[matching-version implementation](https://github.com/chromium/chromium/blob/142.0.7444.134/ui/views/win/hwnd_message_handler.cc).

The trace shows `SC_MOVE|HTCAPTION` completing with button 1, then a repeated
system command after release. Wine sends a second `_NET_WM_MOVERESIZE` with
button 0. KWin starts another grab with no release event left to end it. Further
pointer movement changes the window position from (180,150) to (360,250).

Patch 0033 ignores mouse-initiated size/move requests with no button held.
Explicit keyboard requests still reach KWin. Four alternating drags, a stale
mouse request, keyboard move and keyboard resize pass. Removing only the private
X11 driver makes the same drag regression fail again.

## Retained composition was overwriting Electron's image

Longer testing exposed a separate redraw failure after the drag fix. The old
child compositor passed only 16/32 display samples; disabling it passed 32/32.
Its wrapper was installed on ordinary windows that had never acquired retained
composition content. Movement dirtied that wrapper and flushed its unrelated
GDI bitmap over Electron's image.

Patch 0034 delays wrapping until a window tree owns a composition target or
retained layered-child surface. Promotion follows the normal window/DC update
path, guards reentry, and preserves the existing base image. Already promoted
roots stay retained so detaching the last target can erase old output.

The patched Electron matrix passes 32/32 final-display samples across initial
drawing, four drags, keyboard move/resize and minimize/restore. The unchanged
composition suite passes 8/8 executions and 241/241 samples. Four new colored-base
samples additionally verify preservation through attachment, repeated publication
and detach. The first draft of that test painted before initial background erase
was dispatched; the corrected test drains initial paint messages first. Failed
controls and preliminary test failures are retained with the passing evidence.

The Electron fixture uses `--disable-gpu --disable-software-rasterizer` to isolate
window behavior from failing headless SwiftShader/EGL initialization. The Desktop
launcher retains its existing `--disable-gpu` argument; the extra isolation flag
was not deployed. Neither hardware acceleration nor WebGL is validated here.

## Native replay and Desktop handoff

The native tray replay registers/exercises its icon, queues window growth to
414x346 and restoration to 350x314, and completes with zero access violations.
The previously recorded deferred `80070001` diagnostic remains. Debugger
completion does not prove every menu action works.

A separate candidate installation passes combined agent/UI startup, all five
read-only routes, two ACTIVE devices (G502 X PLUS and G915 X LIGHTSPEED TKL), and
scoped shutdown. Its log contains no unhandled exception or page fault.

The existing `~/Desktop/G HUB (Tested Wine).desktop` now uses the updated Shcore,
X11 and Win32u components. Updating the stopped installation holds its launch
lock, preserves profiles/settings, and saves replaced components for rollback.
All 40 pinned files verify; the Desktop entry validates. No application was
opened on the user's desktop for these acceptance checks.

For this bounded report, implementation coverage is **3/3 defects with targeted
fixes and isolated tests**; post-change user-desktop acceptance is **0/3**. The
reproduced causes have high confidence, but additional NVIDIA/KWin-specific
symptoms remain possible. These ratios do not estimate whole-application
completion. Physical G-Shift, native Linux input output, persistence, automatic
profile switching and full menu/panel behavior retain separate acceptance work.

One earlier smoke-test preparation stopped an already-running user G HUB session.
That was an error against the no-live-desktop-testing instruction. Subsequent
reproductions and candidate-launch checks used isolated displays and prefixes.
No dependency upgrades, ZFS changes or unrelated cleanup were performed.
SystemSage and passwordless sudo responded. Working free space stayed around
54 GiB; Captures had around 442 GiB before archiving.

Evidence, rollback files and a hash manifest are saved under:
`/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/desktop-crash-drag-redraw-20261012/`.
The reusable fixture preparer, test runner and command probe are in `tools/wine`;
patches 0032–0034 and their builders reproduce the source changes.
