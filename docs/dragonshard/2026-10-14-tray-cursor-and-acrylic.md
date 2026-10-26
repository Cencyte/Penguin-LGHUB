# Chapter 20 — cursor ownership and the frozen acrylic fallback

The next two reported symptoms were an invisible cursor over the native tray menu
and a desktop-image rectangle beside it. Investigation on October 14 used private
Xvfb/KWin displays, a disposable prefix, disabled device drivers and loopback-only
network namespaces. The user's running desktop was not driven or stopped.

## Findings and changes

Composition-routed mouse messages targeted a message-only input sink. Cursor and
activation negotiation must instead reach the visible presentation window. Patch
0037 supplies that separation; the tray helper handles WM_SETCURSOR on its host
and DesktopChildSiteBridge. Native XFixes captures confirm a nonempty cursor.

Patches 0038–0039 give WS_EX_NOREDIRECTIONBITMAP composition hosts an alpha-capable
X11 surface and avoid compositing a stale GDI backing image. Regression tests use
actual XComposite pixels, including semitransparency, background repaint without
another commit, movement, detach, and rectangular hit testing.

That necessary composition repair did **not** remove the native tray's rectangle.
The existing DragonShard corpus identified the actual source: tray function
`1400d9380` (`setFallbackAcrylic`) explicitly captures the desktop with BitBlt,
encodes the bitmap and displays it in `bgImage`. Native setDesiredSize at
`1400d8a70` uses a 350-pixel host; the frozen image could occupy the uncovered
portion beside the narrower menu contents.

The final resource repair adds only `bgImage.Opacity=0`, preserving its existing
visibility, stretch, load path and sizing contribution. It preserves the native
executable exactly. The full menu now paints a solid white fallback across its
host. This deliberately sacrifices the broken screenshot-based acrylic effect;
it does not provide live blur. Experiments that skipped capture or changed layout
were withdrawn because they produced a short/blank menu.

`prepare_tray_background.py` validates exact original XBF/PRI hashes, inserts the
XBF property, updates substream lengths, relocates the resource section and
following sections, and verifies all 8,109 PRI payloads: exactly one changes and
all other 8,108 remain byte-identical. The loaded resource is embedded in
`resources.pri`; editing only loose MainWindow.xbf is insufficient.

Format references: Microsoft's [XBF node definitions](https://github.com/microsoft/microsoft-ui-xaml/blob/main/dxaml/xcp/core/inc/ObjectWriterNodeType.h)
and [MRTCore file structures](https://github.com/microsoft/WindowsAppSDK/blob/main/dev/MRTCore/mrt/mrm/include/mrm/common/file/FileBaseSections.h).

## Evidence and deployment

- Final native visual test: **90/90 checks**, including actual text pixels in all
  five no-device menu rows at both opening positions, visible XFixes cursor,
  solid background after underlying repaint, hover survival and dismissal.
- API/anchor tests: **19/19** pointer-transform and **151/151** anchor checks.
- Composition input-binding and child-pixel regressions: zero failures.
- Resource generator reproduces the validated assets byte for byte; corrupted,
  already modified and mismatched inputs are rejected.
- The full menu-action test still fails: clicking Close G HUB does not exit.
  `--placement-only` explicitly excludes that assertion; it is not a full-tray PASS.

Evidence is under `wine-lab/codex-run/tray-surface-20261014/`, especially
`solid-cursor-content-test`, `unit-final`, and `regression-final`.

Five validated files were atomically replaced in `desktop-tested-20261012` for
its next clean launch: win32u.so, winex11.so, the dispatcher helper, MainWindow.xbf
and resources.pri. The launch manifest now verifies **44 pins**. The existing
runner, native executable, user32, other compatibility components and running
processes were preserved. Rollback copies plus the original manifest are in
`live-before-solid-cursor/` beneath the evidence directory.

Measured acceptance: visual symptoms **2/2** pass isolated checks; desktop-user
acceptance of this revision **0/2 reported**. Native command activation **0/1**
passes. These denominators describe this repair, not overall G HUB completeness.
The immediate remaining obligation is the pointer/gesture command path.

## Native gesture engine: smaller-path experiment

The standalone `ninput_native_probe.cpp` now reaches a genuine tap callback from
the user's native Windows ninput.dll. Its SHA-256 is
`0da856260c5bf440038bd7df9e0b002c55be5e540294635b87f44da0fecab90c`.
This does not validate the full application's routing.

Fetching the matching public user32 symbols from Microsoft's symbol server was
more efficient than continuing to guess unnamed exports. Exact PDB:
`user32.pdb/1622FA8BA268948EAB8F7DEE3DF9DF581/user32.pdb` (1,126,400 bytes).
`llvm-pdbutil dump -publics` confirms:

- ordinal 2518, RVA 0x461d0: **RegisterNaturalInputHandler**. It caches whether
  loading the configured NaturalInputHandler from the system directory produces
  a DefaultInputHandler export. Despite its name it takes no parameters here.
- ordinal 2560, RVA 0x4c110: **GetHimetricScaleFactorFromPixelLocation**. Native code
  validates the device/output pointers, supplies scale numerators 1/1 on the
  single-monitor/per-monitor-aware fast path and otherwise calls into win32u.

The private probe patch checks that NaturalInputHandler is actually absent and
that the test display has one monitor. It exits outside those verified cases.
It is **not** deployed and is not a general implementation of either API.

The successive standalone results are useful negative controls:

1. Original Wine: native DLL initialization aborts on user32 ordinal 2518.
2. Absent-handler branch: context creation and configuration succeed, then a
   null source device gives **INPUT_E_PACKET (0x80400003)**.
3. Non-null synthetic device without geometry gives **INPUT_E_DEVICE_INFO
   (0x80400006)**, traced to GetPointerDeviceRects.
4. A geometry fixture reveals ordinal 2560.
5. With the single-monitor scale branch too: down/up packets both process
   successfully; native callback reports TAP, flags BEGIN|END, PT_MOUSE, count 1.

The fixture supplies only its own 1024x768 synthetic device geometry. The real
application must obtain equivalent information from a delivered pointer sample.
No success is manufactured by calling its tap callback directly. Probe code and
negative controls are retained; proprietary DLLs/PDBs remain in the ignored lab.

Two real native menu replays retain the passing visuals but fail Close. Pointer
down/up delivery is logged, then native thread 0x744 raises **0xc06d007f** (missing
delay import) before ninput callback registration. This is the next boundary to
identify, using a debugger attached only to the disposable tray process.

## Native gesture handoff result

The next replay captured the missing boundary precisely. Native `ninput.dll` received
both real pointer packets, but `ProcessBufferedPacketsInteractionContext` returned
`0x80400006` (`INPUT_E_DEVICE_INFO`) because Wine's `GetPointerDeviceRects` looked
for a sample in the querying thread's TLS. The native gesture worker is a different
thread. The packet's source device and pointer data were valid; the geometry lookup
was not shared across threads.

The opt-in compatibility patch now gives the composition mouse a process-wide
virtual device geometry, retains the actual screen rectangle and rejects other
unknown devices. It also supplies the two verified native user32 delay-import
exports: ordinal 2518 (`RegisterNaturalInputHandler`, returning unavailable when no
handler is configured) and ordinal 2560 (`GetHimetricScaleFactorFromPixelLocation`,
with the verified 1:1 single-monitor/per-monitor fast path). `DwmShowContact` is
present as a harmless success stub. The Windows ninput DLL remains user-supplied and
hash-pinned; the diagnostic forwarding wrapper is not shipped.

Evidence:

- Standalone native gesture probe: tap callback, null-device and missing-geometry
  negative controls all pass.
- Native menu replay with the diagnostic wrapper: 97/97 checks, including Close.
- Release native menu replay without the wrapper: 96/96 checks, including Close.
- Two-monitor Xinerama replay: 140/140 checks, including a menu opened and closed
  on the second head.
- Matched DirectComposition regression with pointer assertions: input-binding and
  child-pixel checks both exit 0; 14 down/up pointer pairs and cross-thread geometry
  checks pass.

The resulting launcher is `desktop-tested-gesture-20261014`; the existing Desktop
entry points to it. The currently running G HUB process was not stopped or modified.
Quit that session from its own control action, then launch the Desktop entry to test
this revision. The visual fixes remain solid-background and visible-cursor changes;
this gesture patch is enabled only in the pinned tested launcher.

## October 15: device promotion regression and native tray navigation

The user reported an empty device list and every tray row failing to respond.
The running agent's startup snapshot contained no devices, and its log showed
winebus and wineusb failing to load. The promoted gesture launcher's environment
still contained `winebus.sys=d;wineusb.sys=d`, inherited from the isolated replay.
Both real Logitech devices remain present and accessible through host hidraw.
The Desktop manifest now removes those two exclusions for the next clean start.
Preparation strips them on promotion and refuses to install a Desktop entry
that still carries them. The test installation deliberately retains them.

The earlier two-monitor test was inadequate: Wine's no-RandR handler reported
both Xephyr/Xinerama heads at the same origin. The replacement uses a private
Xorg dummy driver with real RandR outputs, with device auto-attachment disabled.
It reproduces the workstation's lower primary head and upper head: Wine reports
`(0,0)-(1920,1080)` and `(0,-1080)-(1920,0)`. Every input/capture coordinate is
translated between Wine's desktop origin and the private X root. The test now
also rejects completely black rectangles as evidence of menu text.

The extended harness uses the installed KDE `xembedsniproxy` on its own D-Bus,
a minimal private StatusNotifierWatcher, and XTest clicks through `xdotool`.
No live desktop input, window activation, or screenshots are used. It tests
the four navigation rows after closing the frontend, followed by Close G HUB.
The test waits for a newly shown Electron window's startup focus requests to
settle before the next close/open cycle; immediate cycling can otherwise leave
the next popup dismissed by a late frontend activation.

That broader test exposed a concrete navigation failure: the agent called
ShellExecuteExW on `lghubapp://home`, which returned **31 / SE_ERR_NOASSOC** and
displayed a modal error. Electron's HKCU registration exists, but this Wine
10.20 HKCR implementation resolves the machine Classes tree. Startup now
registers the protocol under HKLM **inside this prefix**, with the same
`--disable-gpu` frontend option as the Desktop launcher. It fails startup if
registration fails instead of silently leaving broken tray actions.

The corrected instrumented native run passes **170/170** checks. Its shell trace
observes the original agent dispatching all four routes: `lghubapp://home`,
`lghubapp://glauncher`, `lghubapp://community`, and `lghubapp://profiles`; each
reopens the frontend. The final Close click terminates the tray with exit 0.
Four preparation/error-path regression tests also pass.

Removing Wine tracing then exposed intermittent focus losses: navigation or
Close could fail after the frontend was closed. The private test itself owned
windows but blocked their thread on `getchar()` while the Python driver sent
input or captured screenshots. Foreground-event records showed activation of
the outside test window interleaved with the newly opened popup. The harness
now reads acknowledgements on a separate thread and pumps window messages
while awaiting them, including during the final process-exit wait. This fixes
an invalid test arrangement without suppressing native popup deactivation or
forcing foreground state. Two corrected no-Wine-tracing runs each pass
**171/171** checks, with all five standard actions succeeding
(`navigation-pumped`, `navigation-pumped-repeat`). The preceding
failures remain saved in `release-final`, `navigation-focus-events`, and
`navigation-xevent-trace`; these isolated passes do not establish reliability
on the user's live desktop or cover physical-device-specific menu rows.

The next-launch Desktop runner has been updated atomically and its manifest
rehash verified (47 pinned components). Rollback copies of the previous runner
and manifests are in the evidence directory. The existing session retains its
old driver exclusions until the user selects the Desktop entry's **Quit this
G HUB session** action and launches it again. Device services remain configured
for demand start, and both Logitech devices retain their per-device hidraw
settings. Update freezing and the current live processes are unchanged.

Evidence: `wine-lab/codex-run/tray-x11-20261015/`, including
`navigation-shell-trace` (missing association), `navigation-settled` (corrected
native actions), and `repair-evidence.json`. These results do not establish
that the user's previously observed all-entry failure has the same sole cause.
Real device enumeration and all-entry acceptance on the live Plasma session
remain user-operated checks after a clean restart of this launcher. No claims
about overall G HUB completion follow from these local test percentages.
