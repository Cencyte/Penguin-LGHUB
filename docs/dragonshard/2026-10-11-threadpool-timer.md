# Chapter 8: WinRT timers, and the display failure behind the apparent memory crash

`Windows.System.Threading.ThreadPoolTimer` now activates in the private Wine
10.20 stack. Patch 0030 implements the four factory methods, timer properties,
real elapsed callbacks, cancellation and completion. The native G HUB tray passes
its former class-registration failure. Its next observed failure is in the
composition display path; full tray operation remains unverified.

## Timer implementation and acceptance

The patch adds the WinRT interfaces/delegates to Wine's IDL, registers the runtime
class, and provides a separate timer activation factory in `threadpoolwinrt`.
ThreadPool's original factory does not acquire unrelated timer interfaces.

Each scheduled operation owns a timer reference independently of the caller.
Cancellation disarms the native timer and submits a preallocated cleanup work
item. That work item drains running callbacks before invoking the completion
handler and releasing delegate references. Application callbacks run outside
internal locks. Cancelling or releasing the timer inside its elapsed callback
therefore cannot synchronously wait for itself. A completion handler can retain
the completed timer and continue reading its properties.

Completion is tied to expiration/cancellation, not to the caller dropping the
last reference. Zero and sub-millisecond periodic intervals act as one-shot
requests. These behaviors follow Microsoft's
[creation/completion contract](https://learn.microsoft.com/en-us/uwp/api/windows.system.threading.threadpooltimer.createtimer)
and [ThreadPoolTimer remarks](https://learn.microsoft.com/en-us/uwp/api/windows.system.threading.threadpooltimer).
Periodic intervals exceeding the native TP_TIMER DWORD-millisecond range are
explicitly rejected instead of wrapping. Negative durations are rejected; exact
Windows edge-case/error-code parity still needs differential validation.

The independent MinGW ABI probe passes **585/585 assertions**. It exercises all
four creation methods, ordinary one-shot/periodic delivery, cancellation before
expiration, zero/sub-millisecond periods, completion while a caller retains the
object, completion ordering behind a blocked callback, reentrant cancellation,
release inside a callback, delegate release, and 128 rapid cancellation/release
races. A control run registers the class against the original Wine DLL and still
fails activation with `80040111`, proving registry changes alone are insufficient.

Measured implementation coverage is **4/4 creation methods (100%)**; measured
assertion acceptance is **585/585 (100%)**. This is not 100% WinRT conformance.
No live Windows differential run or cross-apartment delegate-marshaling parity
test was performed. Native G HUB acquires the timer statics in the replay but
creates **zero timer instances** before its separate display failure; native
callback execution must not be inferred from the isolated test results.

## Native composition failure: evidence rather than a nearby stub guess

Three valid native replays pass timer activation and reach 31 successful overlay
initializer returns and 62 overlay entries, with no access violation. The last
replay adds a breakpoint at dwmcorei RVA `899f8` to record native error reports,
plus exception parameters, raw stack candidates, and the compositor's retained
error/counter state. Stack candidates are explicitly not an unwound backtrace.
All breakpoints validate instructions against the pinned dwmcorei binary first.

The final evidence ties together:

1. `dwmcorei!1800a5ae8` creates a DXGI factory, then calls vtable offset `f0`:
   `IDXGIFactory7::RegisterAdaptersChangedEvent`. On failure it reports source
   line `0x199` (409) in `dwmcore/hw/displaymanager.cpp`. The native trace records
   324 reports of `80004001` at that line. Wine's matching DXGI method returns
   `E_NOTIMPL`. This contract is directly observed failing, not merely suspected
   because its stub appears near the end of a log.
2. The compositor accumulates `88980006` (`MILERR_DISPLAYSTATEINVALID`). At the
   fatal exception its retained HRESULT is `88980006` and failure score is 62.
   The saved `18003b51c` implementation increments the score by two on these
   failures and enters diagnostic collection above `0x3c` (60).
3. Diagnostic collector `1800f8460` issues D3DKMTEscape type 9. Wine's unhandled
   escape branch returns `c0000017` (`STATUS_NO_MEMORY`). Native error reporting
   converts this to `d0000017`; the final error report at RVA `f855a`, source line
   `0xe21` (3617), contains that value. The fatal `e0434e49` follows. This is a
   software-generated memory-failure status, not evidence the host ran out of RAM.

These addresses and method bodies came from the existing DragonShard database;
no further whole-binary analysis was necessary. The probe records 1,823 native
composition error reports. Fixing adapter notifications is a justified next
implementation target, but its sufficiency for every display-state failure is
not established. Several other display calls still return unsupported statuses.
Do not suppress native error handling or fabricate diagnostic success to claim
this chain is fixed.

Both final Commits report `shared_regions=0` and `bound_windows=0`. There are still
**zero verified nonempty native input scenes** and **zero end-to-end interactive
tray acceptance runs**. The earlier component input tests remain valid but do
not supply those missing native checks. A percentage of all remaining G HUB
compatibility work cannot be measured from these startup traces; the timer
percentages above deliberately have narrower, known denominators.

## Reproduction and durable evidence

Build the module and standalone regression with:

```sh
bash tools/wine/build_private_threadpool.sh \
  wine-lab/codex-run/wine-routing-source wine-lab/codex-run/wine-routing-build
```

`run_private_build.py --threadpool-build-dir` mounts the built DLL over both the
installed-module lookup location and the copied-prefix DLL inside its temporary
mount namespace. It requires a build marker. Use the existing matched composition,
DXGI/D3D11/WineD3D, DirectWrite, UI and storage options for native replay. The
normal desktop launchers and system Wine DLLs were not changed.

`run_threadpool_tests.py --executable EXE --output NEW_DIRECTORY` runs inside that
private runner, requires a marked disposable prefix, creates Xvfb, registers the
class, records module/test hashes and shuts down the prefix's Wine server.
`run_overlay_trace.py` now registers the timer in the disposable prefix and records
its hash in native replay manifests. All tests use owned Xvfb displays.

Always rebuild `tray_overlay_trace.cpp` before using it:

```sh
x86_64-w64-mingw32-g++ -std=c++17 -O2 -Wall -Wextra -Werror -static -municode \
  tools/wine/tray_overlay_trace.cpp \
  -o wine-lab/codex-run/threadpool-trace-build/tray_overlay_trace.exe
```

An initial replay mistakenly used the old `overlay-trace-build` binary. Its hash
differed from the previous successful replay, and it failed on the already-fixed
terminated-peer suspension race before reaching the timer. That failed evidence
is retained separately; it was not sudo/MCP access loss and not an application
regression. Rebuilding the committed probe resolved it. Subsequent diagnostic
probe extensions were also compiled and exercised in actual native replays.

Archive:
`/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/threadpool-timer-20261011/`.
It contains control/regression/native logs, exact final patch/source/binaries,
retrieved corpus functions, a structured observation and a checksum manifest.
The final replay is `runs/threadpool-timer-error-chain`. Earlier valid replays
without error-entry instrumentation and the failed stale-probe run remain
separate. Their input manifests record the probe hashes actually used.

## Next acceptance sequence

Implement DXGI adapter-change event registration/unregistration with real handle
ownership, cookie lifetime, and notification on actual topology change. Validate
cancellation/teardown and factory-currentness against that behavior, then replay
the same native display-manager call. Follow any remaining display-state failure
to its exact contract. The type-9 diagnostic escape's inaccurate memory status is
also recorded, but should not distract from the preceding display errors.

After the native display path succeeds, require nonempty input-region publication,
actual hardware event delivery and user-driven interactive tray acceptance.
The bulk decompilation corpus is already adequate for this bounded investigation.
