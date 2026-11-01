# Wayland restore investigation

> Historical investigation record. For current installation, X11/Wayland profiles and validation status, see the [installation guide](release-installer.md) and [recorded environments](../../packaging/tested-environments.json).

**Latest acceptance and scope:** the user confirms the red shutdown banner is
fixed, reports that the windowing anomalies remain, and explicitly declares
those anomalies out of scope. Do not claim the quit expansion or brief restore
flash is fixed, or continue pursuing them as part of the CachyOS deployment.
The startup correction and accepted runtime remain available. The detailed
isolated results below are historical evidence, not visual user acceptance.

**Earlier finding:** the user rejected the repaint-only mitigation below after
a fresh login and cold G HUB launch. Read-only inspection then found Plasma
reporting a normal, focused window while Wine still reported it minimized at
`(-32000,-32000)` with a `160x31` client. This is a confirmed window-state
disagreement, not merely missing textures. The follow-up section records the
Wine correction and the remaining real-session acceptance gate. Earlier test
results remain here as history, not evidence that the rejected mitigation works.

## Initial repaint investigation

The reported black window is consistent with a presentation failure after
minimize/restore. The managed installation hashes still verify; there is no
evidence that the protected Logitech depots disappeared again. The existing
lazy-retained-surface and X11 image-flush patches are already in the packaged
Wine build. This change adds a narrow, opt-in frontend repaint mitigation;
it does not establish the exact Wine/KWin race or claim live-session resolution.

## Implementation

`tools/wine/prepare_frontend_startup.py` retains the pinned input hash checks,
font initialization, tray logging, and color-picker transformation. It adds
main-window `restore` and `show` listeners. When the Wayland worker sets
`PENGUIN_GHUB_REPAINT_ON_RESTORE=1`, these request a full Electron repaint on the
next event-loop turn and once more after 250 ms. Hiding, minimizing, or closing
cancels pending requests. There is no continuous repaint loop, resize, page
reload, background-throttling change, or change to the native tray renderer.
Without the worker's opt-in flag, the hook registers no listeners.

Existing installations can use `desktop_runtime.py stage-frontend --candidate …
--config …`. The command verifies the installed files and original acquisition
candidate, builds a content-addressed archive outside the prefix, and atomically
updates the launch configuration. The next container mounts this verified
archive read-only over `resources/app.asar`, after mounting the application
read-only. The original archive, running processes, and user settings remain
untouched. The previous configuration is retained for rollback.

The shared-runtime lock now explains contention instead of displaying bare
`[Errno 11]`. The original user's error had no traceback, so lock contention is
a plausible source, not a proven diagnosis. Future launcher failures record
the complete traceback in `logs/launcher-errors.log`. No locks are forcibly
removed, and unrelated running sessions are not terminated.

## Evidence and limits

The test uses a private KWin 6.5.6 virtual backend, Xwayland 24.1.9, separate
D-Bus, network, PID, device and filesystem namespaces, and a disposable prefix.
The VM's live desktop is never driven or captured. The virtual backend reports
QPainter; setting `KWIN_COMPOSE=O2` in early experiments did not make it OpenGL.
The final harness explicitly uses QPainter. Physical scanout, VM DRM composition,
and a bare-metal graphics driver are outside this test's coverage.

`wayland_restore_diagnostic.py` completes disposable onboarding, closes the
Chromium debugger, terminates only that prefix's frontend processes using pidfds,
and asks the production supervisor to start a frontend without debugging.
It verifies that no frontend command line retains a debugging switch. Restore
checks compare actual Xwayland window pixels against the initial Devices page,
including a rejection of uniform white/black content. An initial blank page is
also rejected. Merely finding the window or a healthy DOM cannot pass.

Early cases 001/002 kept the debugging port enabled and preserved the UI in
three cycles each. Case 003, after a debugger-free restart, captured a completely
white window in the first restore; subsequent restores recovered. This is a
related blank-frame symptom, not an exact reproduction of the user's black
window under the VM's real DRM compositor. Its old test
receipt incorrectly says passed because pixel scoring had not yet been added.
Case 004 with repaint enabled passed all three scored restores, matching every
sampled nonblack reference pixel. Case 005 with repaint disabled also passed
12/12: the defect is intermittent, and this comparison cannot establish a
statistically reliable improvement rate.

Case 006's launcher-reopen check used a stale XID after Wine replaced its
window. Case 007 corrected that capture issue, passed its first three restores,
then found the window still minimized after a later reopen and did not exit
cleanly within the test's shutdown interval. These are failed tests, not redraw
passes. The final test waits for the actual window-state transition instead
of assuming that a second-process launcher reopen completes within two seconds.

Case 008 exercised the staged read-only overlay and debugger-free frontend
through 12/12 window-manager minimize/restore cycles. Each restored frame
matched all sampled reference pixels. Tray registration, orderly launcher exit,
no remaining prefix processes, and post-exit installation hashes passed.
Together with case 004, that is 15/15 scored window-manager restores with the
mitigation enabled; the separate launcher-reopen failure above is not included.

Case 009 used that bounded state wait and passed 12/12 Desktop-launcher
reopens through the staged overlay, including clean shutdown and unchanged
installation hashes. The completed mitigation-enabled cases therefore contain
27/27 scored restore passes (15 window-manager, 12 launcher-reopen). This is
100% of those measured cycles, not a reliability estimate or proof that the
user's exact black-frame failure is eliminated.

The final receipt is in `wayland-restore-verification.json`. The source tests
pass 103/103 (100% of this packaging test suite, not 100% Wayland compatibility).
The repaint mitigation is implemented; real-session acceptance is pending.
The precise underlying race remains unproven. Nothing was pushed to GitHub.

## Primary sources consulted

- [Chromium 142 software output](https://raw.githubusercontent.com/chromium/chromium/142.0.7444.134/components/viz/service/display_embedder/software_output_device_win.cc):
  `SoftwareOutputDeviceWinDirect::EndPaintDelegated` copies damaged pixels to a
  window DC. The packaged Electron identifies itself as 39.1.2.
- [Electron 39.1.2 WebContents](https://raw.githubusercontent.com/electron/electron/v39.1.2/shell/browser/api/electron_api_web_contents.cc):
  onscreen `Invalidate()` forwards to the owner window.
- [Electron 39.1.2 native window](https://raw.githubusercontent.com/electron/electron/v39.1.2/shell/browser/native_window_views.cc):
  `NativeWindowViews::Invalidate()` schedules painting across the window bounds.
- [Chromium window message handler](https://raw.githubusercontent.com/chromium/chromium/142.0.7444.134/ui/views/win/hwnd_message_handler.cc):
  paint handling and child-rendering-window clearing are separate from renderer
  page state.

Tavily research was attempted but its quota was exhausted; primary sources were
retrieved directly. No Wine, kernel, graphics-driver or host package upgrade was
performed.

## Deployment

The VM's existing `Penguin-LGHUB Wayland Test.desktop` still points to
`wayland-desktop-002`. Its configuration now selects the verified overlay
`frontend/7c8ab612120fe6a22f1814bb12b2048924e8ebd73cd38f54e3836e49256c6a45.asar`.
The stage command verified all managed files before replacing the configuration;
the overlay hash was independently rechecked afterward. Existing running
processes were retained. The VM Desktop note contains the user-driven restart
and minimize/taskbar-restore check. No fresh prefix replaces the user's settings.

## Follow-up: confirmed minimized-state disagreement

The running frontend had loaded the previous `7c8ab612…` archive and repaint
environment flag correctly. Its log contained startup repaint messages but no
later restore repaint. The guard intentionally skips a minimized window, so
adding more repaint calls could not address the state disagreement.

A read-only Win32 query and a narrow read of the matching Wine driver's
structures found:

- Xwayland `WM_STATE=Normal`, focused; Win32 `IsIconic=1`.
- Driver desired/pending/current WM states were all Normal.
- The outstanding geometry request was `1024x768`, but KWin had supplied
  `1024x743`; `configure_serial=4837` remained pending. Other inspected state
  serials were zero.
- `window_update_client_state()` returned before `SC_RESTORE` because it waited
  for that geometry request. A separate read-only monitor query reported the
  current live monitor as `1280x800`, with a `1280x756` work area.

No debugger was attached to the user's app and no live window was moved,
minimized, restored, clicked, or captured. The source and original driver still
hash to the previously pinned build.

### Correction

Patch `0044-x11-restore-state-and-buffered-events.patch` permits a confirmed
Normal-state transition to restore a minimized Win32 window even while the
geometry acknowledgement is outstanding. This branch requires
`WINE_GHUB_RESTORE_STATE=1`; geometry synchronization retains its own guards.
It also checks Xlib's already-buffered events before reporting the driver queue
drained. A final property-event handler can read replies that buffer more events
without leaving the socket readable. The queue issue is supported by code
inspection and the failed private trace; it was not separately proven to be
the cause of the user's live failure.

The Wayland frontend recipe now fits the native window's initial size and
minimum within Electron's monitor work area. On shorter displays it scales the
content to preserve the full logical UI. It ignores minimized `160x31` geometry
and restores normal scaling on larger windows. The existing bounded repaint
hook remains enabled. These frontend behavior flags are limited to the opt-in
Wayland worker.

`build_private_x11.sh SOURCE BUILD --restore-state` applies the added Wine patch
in a separate configured tree. The optional module has a pinned input/output
recipe in `packaging/wine-x11-restore.lock.json`. Debug paths/toolchain can change
a rebuilt ELF hash; the recipe deliberately does not silently trust any rebuild.
The measured candidate is `11b02e4251f06ddb5463e223924d62e5d47a5da7c02cf0aab6084820b3114386`,
with maximum GLIBC requirement `2.38`.

`desktop_runtime.py stage-x11-restore --config … --x11-restore-module …` verifies
the base runtime and candidate, writes a content-addressed per-installation
overlay, retains a rollback configuration, and changes the next cold launch.
The container mounts that module read-only over its private Wine driver; the
base runtime and system Wine are not overwritten. Fresh development preparation
also accepts `--x11-restore-module`. The same staging/mount path is exercised by
the final private test, rather than a test-only driver replacement.

### Expanded validation

The first tests used KWin's QPainter virtual backend and X11-side minimization.
The expanded tests use the VM's DRM render node for private OpenGL/llvmpipe
composition, Windows `ShowWindow(SW_MINIMIZE)`, and taskbar-style X11 activation.
They compare both X drawable pixels and KWin's composed output, and independently
query Win32 minimized state after every transition. The debugger is removed by
restarting the disposable frontend before these checks. Physical input, HID
devices, the live compositor, its bus, and network remain isolated.

| Case | Result | Interpretation |
| --- | --- | --- |
| 011, original driver/repaint, OpenGL | 12/12 restores | Did not reproduce the user's failure |
| 012, original driver, short display | 12/12 restores; 114,668 configuration mismatches | Exposed a repeated size conflict despite passing pixels |
| 013, work-area fit only, repaint disabled | 9/10 scored restores; restore 10 black; following minimize failed | Sizing alone is insufficient; configuration mismatches fell to zero |
| 014 and 016, candidate, simulated panel | Initial composed-pixel check failed | Invalid fixture: KWin centered the panel over the UI; corrected with explicit position hints and a position assertion |
| 015, Wine correction plus work-area fit, repaint disabled | 12/12 restores, matching Win32/Xwayland state and composed pixels | Positive isolated result; not a statistical reliability claim |
| 017, actual installer overlay path, corrected bottom panel, repaint enabled | 12/12 restores; Win32 state, composed pixels, tray registration, clean exit and installation hashes passed | The deployment path is exercised; this mock X11 dock does not reproduce all Plasma panel work-area semantics |

The final run and deployment receipt are recorded in
`wayland-restore-verification.json`. Real-session acceptance remains **0/1**
for this new correction until the user retests the VM. These software-rendered
VM checks do not qualify physical GPUs, native Wayland input, or other desktops.

The new driver's successful cases total **24/24** restore cycles. Packaging
tests pass **109/109**. Earlier passing baseline cases and invalid-fixture cases
are not included in that candidate count. No success rate for arbitrary
desktops is inferred from these samples.

### Updated deployment

The existing VM `wayland-desktop-002` configuration now selects frontend
`47b5c7ec3c6e6277a4ef6ab81d15d23e55fe85b456b1dceb668c6ee33e6267a5`
and Wine overlay `11b02e4251f06ddb5463e223924d62e5d47a5da7c02cf0aab6084820b3114386`.
Both stage commands verified the installation. The pre-update configuration is
retained as `desktop-before-47b5c7ec3c6e.json`; another backup records the state
immediately before selecting the driver overlay. The Desktop test note explains
using the launcher's **Quit this G HUB session** action and then reopening it.
Closing only the window is insufficient to replace a loaded driver. No logout
or package upgrade is required. The live session was left running, so the loaded
driver remains old until the user's cold launch. Nothing was pushed.

Additional primary sources inspected:

- [KWin virtual backend](https://raw.githubusercontent.com/KDE/kwin/v6.5.6/src/backends/virtual/virtual_backend.cpp)
  and [virtual EGL backend](https://raw.githubusercontent.com/KDE/kwin/v6.5.6/src/backends/virtual/virtual_egl_backend.cpp).
- [KWin screenshot interface](https://raw.githubusercontent.com/KDE/kwin/v6.5.6/src/plugins/screenshot/screenshotdbusinterface2.cpp):
  used only inside the owned test compositor's isolated bus.
- [Chromium Windows desktop host](https://raw.githubusercontent.com/chromium/chromium/142.0.7444.134/ui/views/widget/desktop_aura/desktop_window_tree_host_win.cc).


## Follow-up: restore flash and orderly closing

The original two-second settled-frame checks missed a short presentation gap.
`restore_timing.py` now samples the owned Xwayland drawable for the first
750 ms after activation, comparing nonblack UI reference pixels. The samples
also record the window opacity requested from the compositor. This measures
application drawable content and requested opacity, **not physical scanout**;
the sample spacing and readback overhead mean an unsampled flash remains possible.
No live desktop was captured or driven.

The accepted implementation's baseline (case 018) contained an incomplete early
sample in 3/12 restores, recovering in approximately 13–16 ms in those cycles.
Adding a synchronous repaint (019) still produced incomplete samples in 4/12
restores. A frame-subscription/opacity experiment (020), and then a 100 ms paint
allowance after the frame callback (021), failed to eliminate the flash. In 021,
content was complete while transparent, then became blank after opacity returned
to one. The separate software-output process repainted it later. This experiment
made presentation worse and is **not included in the staged implementation**.
The original restore hook and verified Wine driver are retained.

The pinned frontend provides three explicit close indications: the window's
`close` event, its quit routine, and the G HUB agent's `REMOVE /` shutdown
notification. With `PENGUIN_GHUB_GRACEFUL_CLOSE=1`, the new hook hides the main
window before the corresponding renderer cleanup/disconnect notification.
The ordinary close handler retains its existing prevention and cleanup logic;
closing the window must leave the agent and tray available. Unexpected socket
errors are not hidden. The Wayland worker enables the behavior; it is inert
without that flag.

The Desktop Quit supervisor now asks the existing frontend to exit using the
pinned second-instance `--killall` handler. That handler calls `oT(false)`, so
it quits the frontend without recursively killing the backend. The supervisor
waits for frontend exit, with an eight-second overall bound and a five-second
request timeout, before its existing prefix-scoped wineserver cleanup. A failed
request is logged and falls back to the existing cleanup. Case 019 confirmed
window hiding followed by a completed graceful frontend exit.

A separate native-tray **command-line** `--killall` experiment in case 021
reached both explicit frontend shutdown hooks, but did not end the launcher
within 25 seconds. This is not a successful native tray-menu close test. It
also exposed a diagnostic bug: a caught exception could leave a previous
`passed` flag true. The harness now explicitly fails that path; case 021's old
raw top-level success flag is superseded by this failure classification.
Actual tray-menu visual acceptance remains user-driven.

The next useful rendering investigation is the Wine surface/state update that
clears content already painted by Chromium's separate software-output process.
Another frontend delay is not supported by these measurements. This checkpoint
must not be described as eliminating the first-frame flash.


### Final checkpoint and staging

Case 022 uses the final closing hooks and the accepted restore code. All 12/12
settled restores passed, including Win32 state and private KWin composition.
Five of those 12 restores still contained an incomplete early drawable sample;
this is a failed cosmetic presentation check, not a fixed flash.

Both of the two planned automated close workflows passed (100% of those two
workflows, not comprehensive closing coverage): `WM_CLOSE` retained the agent,
tray and supervisor and reopened with matching UI pixels; Desktop Quit hid the
window, completed graceful frontend exit, and left no prefix processes. Managed
installation hashes remained intact. The actual native tray-menu interaction
still needs user confirmation; the unsuccessful command-line experiment above
is not counted as a pass. Packaging tests passed 115/115.

The VM's existing Wayland launcher now selects frontend
`f341b69b9a7c07309d24744c49fb1d0368b128ee4773abaa69270018702340e3`.
It exactly matches case 022's tested archive, despite a source-only refactor
moving the close hook into a testable constant. The stage command verified the
managed installation and retained `desktop-before-f341b69b9a7c.json` for rollback.
It takes effect on the next complete user launch. Settings, the accepted Wine
overlay, and the system packages are preserved. The VM Desktop note describes
the remaining visual checks. Nothing was pushed to GitHub.

## Follow-up: startup hang, quit geometry and launch feedback

The VM's stalled session was a real Wine initialization wait. Read-only process
inspection found the Electron main thread waiting in `TransactNamedPipe` for its
Crashpad child. That child and a second launch were waiting in
`win32u.load_desktop_driver()` for an unbounded `WM_NULL` reply from the Wine
Explorer desktop. PE unwind records placed Explorer's desktop thread inside an
outbound COM/RPC wait. The remote COM callee was not identified; these observed
waits establish the blocked initialization path without claiming a complete
reconstruction of the COM dependency cycle. No dead NFS mount was present.

Patch `0045-win32u-bound-desktop-driver-handshake.patch` bounds that handshake
to five seconds when `WINE_GHUB_DESKTOP_INIT_TIMEOUT=1`. Wine still checks the
desktop GUID and published graphics-driver registry entry before loading the
driver. Without the flag it retains its previous behavior. This is a targeted
Wine 10.20 module overlay; it does not replace system Wine or upgrade packages.
The recipe in `packaging/wine-desktop-init.lock.json` checks the patch, original
module and replacement hashes. `desktop_runtime.py stage-desktop-init --config
… --desktop-init-module …` stages it atomically with a rollback configuration;
fresh preparation accepts the same module option.

The frontend now acknowledges Electron's `ready-to-show` event through an
atomic file with a new per-session token. Launch requests remain coalesced
until that acknowledgement arrives. A stale file cannot mark the next launch
ready. If the first frontend never becomes ready within 60 seconds, the
supervisor cleans up its private session. The outer launcher allows exactly
one cold retry, only for this specific timeout and only after verifying no
owned prefix processes remain. Ordinary exits and other failures never retry.
Legacy frontend archives without this protocol retain their prior behavior.

For explicit Quit and backend shutdown, the existing close hook sets window
opacity to zero before hiding it. Ordinary close-to-tray preserves opacity,
so reopening still works. This change does not touch restore timing or reapply
the rejected restore-opacity experiments.

Generated shortcuts now include `StartupNotify=true` and the Wine frontend's
window class, and installation registers an identical application entry under
the user's applications directory. This allows Plasma to resolve its launch
feedback and icon. X11 startup IDs are completed on readiness or failure using
the freedesktop protocol. Wayland activation tokens are not X11 startup IDs;
Plasma handles their feedback and timeout. Its default busy-feedback interval
is five seconds, so this does not promise a cursor throughout a slow startup.
The cursor's appearance on the user's desktop is not yet visually verified.

### Validation and deployment

Case 023 deliberately suspended only the disposable Wine desktop's thread.
With the new gate disabled, the child remained blocked for the full 8,008 ms
observation and proceeded only after the desktop resumed. With the gate
enabled, it initialized in 5,115 ms while that desktop was still suspended.
The full application then passed 12/12 settled restores, tray registration,
close/reopen, graceful shutdown and installation-integrity checks.

Case 024 deliberately withheld the first frontend. The production supervisor
timed out, cleaned up, retried once and reached the real G HUB window. Its
12/12 settled restore checks and close/reopen checks passed. During Desktop
Quit, 190 samples contained zero visible size changes; opacity reached zero
and the window disappeared after approximately 2,013 ms. This measures X
geometry/opacity, not physical scanout or Plasma's cached animation frames.
Both cases left zero prefix processes and preserved managed file hashes.

Packaging checks pass **126/126 (100% of this suite)**. The two new isolated
application runs pass **24/24 measured restores**; this is coverage of those
samples, not a general reliability percentage. The Wine wait correction and
one-retry path each have a successful fault-injection test. X11 feedback was
also received and decoded on a private Xvfb server. The three requested changes
are implemented/staged; real-session acceptance remains **0/3** until the user
checks launching, quit geometry, and the busy indicator.

The old verified stalled supervisor was stopped and its prefix processes fully
exited. The existing VM Wayland shortcut now selects frontend
`fa22d434c981a0677c9ad3f07a8031a194be3a1aaa1337abeaa8801c1ca2380e`
and desktop-initialization module
`5fb3d5e404c8935cfa8f74f0277188f75c14c7841beecd9c084d010d0601386e`.
The previously accepted X11 restore overlay remains unchanged. Configuration
backups are `desktop-before-fa22d434c981.json` and
`desktop-before-init-5fb3d5e404c8.json`; restore both earlier selections by using
the first backup. Settings are retained. The Desktop shortcut also has a saved
copy in the installation's logs directory. No live GUI was launched, driven or
captured. No host package changed and nothing was pushed.

Relevant primary sources for feedback behavior:
[KIO process runner](https://raw.githubusercontent.com/KDE/kio/v6.22.0/src/gui/kprocessrunner.cpp),
[KWin activation](https://raw.githubusercontent.com/KDE/kwin/Plasma/6.5/src/xdgactivationv1.cpp),
[KWin busy feedback](https://raw.githubusercontent.com/KDE/kwin/Plasma/6.5/src/plugins/startupfeedback/startupfeedback.cpp).
