# Chapter 7: Real input delivery and a reproducible GL recovery fix

The composition experiment now routes hardware messages to retained input sinks.
The separate GL failure has a controlled reproducer and a fix. These are lab
implementations with explicit limits, not a declaration that the native G HUB
tray is fully usable. Its next observed fatal failure is activation of
`Windows.System.Threading.ThreadPoolTimer` (`0x80040154`).

## Composition input: from a retained handle to delivered input

Patch 0028 extends the matched Wine 10.20 Win32u/ntdll/wineserver protocol. An
immutable CompositionScene object retains ordered rectangles and actual sink
objects. A Section owns the published scene; a reader obtains its own handles
from the server. No producer's numeric handle or process pointer is treated as a
valid handle in another process. Window bindings retain scenes through caller
handle closure and are removed during window/thread teardown.

The compositor computes translation, relative layout, rectangular clipping,
nearest bound ancestor inheritance and bottom-to-top visual ordering. Input-only
visuals with an explicit logical size can receive input even without pixels.
Regions are bounded to 4,096 per scene and trees to depth 128. A consumer samples
committed pixels and the scene under the existing resource mutex. Failed reads
leave the previous complete snapshot intact. Pending assignments do not change
the installed routing until Commit.

The relay's private version is now **2**. Pixel-only version-1 readers must reject
it instead of silently discarding its input information. This is a private lab
protocol, not a reimplementation of Windows' internal shared-section wire ABI.
All participants need the new compositor and the matching Wine components.
`WINE_GHUB_INPUT_ROUTING=1` enables the Win32u bridge; code 29 and version
`0x47484931` identify that bridge.

Wineserver picks the foremost eligible region after ordinary window hit testing,
then queues the real hardware message to its sink's owning thread. A private
message flag prevents a second Win32u window hit test from redirecting it away
from a message-only sink. Mouse capture takes precedence. QS_POINTER messages
continue through the existing input-delegation mechanism, including callback
and subsequent forwarding to the owner. The tests use SendInput or Wine's
existing driver-to-server hardware path, never PostMessage to imitate input.

The supported sink contract is deliberately bounded: flags zero, matching first
four routes of type zero or HWND, and a fifth route either empty or matching.
Nonuniform and other route-category contracts remain unsupported. Validation
also visits clipped/empty trees before any shared frame is written, so these
configurations cannot silently erase a binding. Failure tests retain the
previously published blue frame when a new red frame contains such a sink.

The existing corpus supplied specific reference points rather than another bulk
analysis pass: dwmcorei's parent-sink lookup at `180103f64`, input traversal at
`18007d6b0`, interaction command at `180099a88`, and dcompi's input command at
`1800018cc`. Ancestor association in that evidence does not establish every
Windows hit-test or route-category rule; those remaining rules are not asserted
as perfectly replicated.

## GL recovery: failure, cause, fix

The historical tray log reported a backup-context failure with Win32 error
`0x7d0`, followed by invalid-context shared-texture publication/import failures.
Simply destroying the original swapchain window did **not** reproduce it: that
baseline test passed 25 checks. An initial native replay also did not reproduce
the failure. Neither result was treated as a fix.

A controlled, explicitly enabled recovery injection made the failure repeatable.
After an application DC receives Wine's internal pixel format, it forces the
normal recovery branch to acquire a fresh backup DC. The old code carried
`internal_format_set=1` to that new DC. With GetPixelFormat returning zero, it
incorrectly skipped setting the new DC's pixel format. wglMakeCurrent then failed
with the same `0x7d0`. The controlled baseline failed device creation.

Patch 0029 resets `internal_format_set` when the DC is replaced, alongside the
existing reset of `dc_has_format`. The same injected test now passes all 25
checks: consumer readback before destruction, consumer readback after destruction,
and producer readback all contain zero mismatched pixels. Default shared-texture
and EnqueueSetEvent regressions also pass. The fix addresses this demonstrated
recovery defect; it does not prove that every possible GL failure is eliminated,
or that every occurrence in the earlier native trace had this exact cause.

`GHUB_D3D11_TEST_BACKUP_DC` is a test-only, once-per-process injection and is
absent from native replays. `run_graphics_tests.py --window-loss-only
--force-backup-dc` requires proof in the log that injection actually executed.
`GHUB_D3D11_CONTEXT_TRACE` provides diagnostics independently. Tests and replays
run on owned Xvfb displays, with llvmpipe reported by the native replay, not on
the live workstation display.

## Measured coverage and limits

The version-2 regression run contains:

| Check | Result |
|---|---:|
| Composition suite executions | 8/8 pass |
| X-server pixel samples | 133/133 pass |
| Routing cases (18 local/shared-state cases, separate-process delivery, delegated pointer delivery) | 20/20 pass |
| Clock assertions | 165/165 pass |
| Kernel sink assertions | 452/452, plus 7/7 child assertions |
| Delegation assertions | 618/618, plus 6/6 foreign-process assertions |
| Window destruction assertions | 579/579 |
| Disabled sink / delegation assertions | 2/2 and 7/7 |
| Forced backup recovery assertions | 25/25 after fix; baseline failed |
| Normal window-loss / shared textures / completion events | 25/25, 121/121, 505/505 |

These are **100% of the named regression checks**, not 100% of Windows composition
or G HUB compatibility. The important unvalidated scope remains actual user
interaction in the native tray, mouse-to-WM_POINTER promotion and complete
pointer/contact state, nonuniform routing categories, arbitrary transforms,
Windows-equivalent activation/focus policies, and atomic multi-resource/window
transactions. Individual shared snapshots are coherent; full cross-resource
Commit atomicity was already outside this backend and remains so.

The native run constructs both overlay contexts without the previous null-overlay
access violation. It no longer reports the blanket input-publication guard. It
then reaches the missing ThreadPoolTimer activation and fail-fast exit
`c0000602`. A debugger's zero exit status only means tracing completed. The
native application's exit and successful interactive tray acceptance are separate
facts. In the final replay, both recorded Commits return success, but report zero
published input regions and zero bound input windows. One temporary validation
scene is created. Thus the native application has **not** demonstrated a nonempty,
usable input scene before its timer failure, despite the successful component
routing tests. After implementing the timer, measuring native scene geometry and
actual event acceptance is still required. That replay has 22 successful overlay
initializer returns and 44 overlay entries, with no access violation.

An extra diagnostic replay failed inside the probe while suspending a peer thread.
Wine's server returns ACCESS_DENIED for an already terminated thread whose debug
exit event can still be pending. The probe now opens a synchronizable handle and
skips a failed suspend only if that handle is signaled. Other suspension failures
still abort. The failed run is retained; this was not a sudo/MCP access loss.

The efficient next investigation is the exact ThreadPoolTimer activation contract:
registration, factory methods, one-shot/periodic callbacks, cancellation and
completion lifetime. Wine 10.20's `dlls/threadpoolwinrt/main.c` factory currently
recognizes only ThreadPool, confirming this needs implementation as well as
registration. Returning a fabricated timer or registering a class with no
working timer behavior would only move the crash. Use the existing corpus and
Wine source for that bounded task; no full DragonShard rerun is justified.

## Reproduction and durable evidence

Build the input stack with `build_private_window.sh SOURCE BUILD --input-routing`.
It applies patches 0003–0005, 0011, 0013–0017 and 0028 as required, regenerates the
private server protocol, and builds matched Win32u, ntdll, wineserver and User32.
Build the graphics stack with `build_private_d3d11.sh SOURCE BUILD --enqueue-event`,
which also applies 0029. Build the compositor with `build_dcomp_experiment.sh`.
Use `run_private_build.py --composition-build-dir` for the matching private mount
namespace; never combine one new Win32u with an older server/ntdll pair.

The final compositor is `wine-lab/codex-run/dcomp-routing-final-20261011`.
The matching stacks are `wine-routing-build` and `wine-d3d-context-build` under
the same lab. The normal launchers and installed Wine components were not changed.
The test drivers are `run_dcomp_tests.py`, `run_composition_input_test.py`,
`run_graphics_tests.py` and `run_overlay_trace.py`; all own their private display
and use stopped, separate lab prefixes.

Durable archive:
`/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/composition-routing-20261011/`.
It retains successful and failed runs, exact final sources and binaries, the
controlled pre-fix WineD3D binary, hashes, native component manifests and
`observation.json`. The catalog links this runtime evidence separately from the
completed static corpus. Early routing-test failures are retained: one invocation
copied the stale guard-only executable, and two apparent missing clicks were
WM_LBUTTONDBLCLK messages omitted by the test's original counter. The corrected
test counts those as delivered clicks; the actual routing path was not weakened.
