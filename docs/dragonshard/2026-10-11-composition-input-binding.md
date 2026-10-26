# Chapter 6: Input ownership and the first successful overlay initialization

The native tray now constructs both overlay contexts and completes the previously
failing initializer. In this headless replay, the same target returned success
31 times and entered its two non-null overlays 62 times. The old null-overlay
access violation did not occur. This is a concrete initialization milestone;
it does not establish successful input publication or a responsive tray.

## What changed

Private `IDCompositionVisualRestricted::SetInput`, slot 35, now validates and
retains a real Wine composition input sink. The implementation duplicates the
caller handle, queries the duplicate's kernel LUID, and rejects wrong object
types, missing read access and invalid handles. A caller may close its original
handle immediately. Numeric handle reuse cannot redirect the retained binding.
Repeated assignments of the same LUID do not accumulate owned handles.

Visuals retain separate pending and committed bindings. Replacement and clearing
preserve the previous committed sink until a successful Commit. Destruction
releases both, including when the public device reference was released first.
This is a conservative lab ownership model; Windows' exact validation timing
and error-code choices have not been differentially established.

The existing frame transport contains pixels, not hit-test regions or sink
associations. Existing delegated input handles `QS_POINTER`; it does not select
a composition visual. Consequently a Commit containing an input-bound local or
shared target now explicitly returns `E_NOTIMPL` before publishing any frame.
The guard covers nested, clipped, hidden and zero-sized trees. Detached visuals
can commit their retained association. We deliberately do not report success
for an interactive frame whose input would be discarded.

The Windows.UI InputSite bridge's getter interfaces describe WinRT composition
visual adapters. They are not interchangeable with the restricted DComp visual
interface. This change does not advertise those unrelated getter ABIs.

## Evidence and validation

The prior corpus identifies native dcomp's SetInput at RVA `1e60`, forwarding to
VisualSetInputSink at `3520` and its opcode `0x16` command. InputStateManager's
sink creator at `18da0` supplies four type-2 HWND routes. Its attach helper at
`18ff0` passes the resolved sink to restricted visual slot 35. The new runtime
directly confirms that the argument is a queryable Wine Composition object:

- Visual: `7fb71ae37020`.
- Caller handle: `3fc`; retained duplicate: `57c`.
- Kernel LUID: `00000000:0000044e`.
- Full render target: `7fb78eb4d1d0`.
- Created overlays: `7fb78ea8c280` and `7fb78ea8c320`.

The SHA-pinned debugger records both construction sites, successful initializer
epilogues, and subsequent calls using those same overlay pointers. There are
zero recorded access violations. No return value or native object field was
rewritten. Native execution subsequently encounters the input-publication guard
and the existing invalid-GL-context/shared-texture error. The child ultimately
exits with `e0434e49`; the exact causal path to that exception is not established
by this probe. Debugger exit 0 means its observation completed, not tray success.

The final regression runner passes **8/8 executions**: clock, two device ABI
probes, two native FrameworkUdk wrapper probes, ordinary rendering, input binding,
and embedded-child rendering. All **133/133 actual composited X-server pixel
samples pass**: 102 ordinary, four input-publication guards, and 27 child samples.
Input tests use an independently declared slot-35 call and real wineserver
object handle counts, not implementation headers or a mock ownership counter.
They cover close-before-Commit, duplicate identity, replacement, clearing,
rejected assignments, failed-Commit retention, destruction and publication guards.
Pixel checks prove rejected commits leave the old shared frame intact, including
when a different local target on the producer device causes the rejection.

The first test attempt passed the lifetime tests and existing rendering suite,
but all four new pixels failed: their imported visual lacked the 16-pixel inset
expected by the shared capture helper. The corrected setup passes the complete
suite. Both attempts are retained. The DLL and tests compile with
`-Wall -Wextra -Werror`; Python syntax and whitespace checks pass.

## Progress and what comes next

Measured initializer construction coverage is now **2/2 required overlay objects
(100%) in one observed replay**, with 31/31 successful initializer returns on one
target. These repetitions are not 31 independent startup trials. The new binding
and guard tests all pass. Actual tray input delivery remains **unvalidated**;
there is no defensible overall G HUB compatibility percentage from these counts.

The next composition implementation should replace the guard with an actual
publication and routing path. The work is specific:

1. Recover the route-category and dispatch contract from the retained corpus.
   Targeted starting points include dcompi's
   `CApplicationChannel::SetVisualInputSink` (`1800018cc`, SHA `57c078371a1e...`),
   and dwmcorei's input-sink context traversal (`18007d6b0`), parent lookup
   (`180103f64`) and interaction command (`180099a88`). These are leads,
   not a claim that all describe the identical command path.
2. Publish the supported visual hierarchy, translated/clipped hit regions,
   ordering and sink identities alongside frames. Raw process-local handles
   must not be copied into another process as usable handles. Bind validated
   kernel objects and retain their lifetimes through publication and teardown.
3. Connect selection to Wine's real input queue and existing delegation path.
   Test routed versus unrouted coordinates, overlapping children, reassignment,
   capture, target/window destruction and caller-handle closure on private Xvfb.
   Remove the publication guard only for behavior demonstrated by those tests.
4. Diagnose GL context/shared-texture lifetime separately, then replay the tray
   for visible content and actual interaction acceptance. Successful overlay
   allocation is no longer the acceptance criterion for those later stages.

This uses the completed DragonShard corpus for bounded retrieval rather than
restarting bulk analysis. No new host deployment is implied by the lab result.

## Durable artifacts and reproduction

Archive:
`/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/composition-input-20261011/`

It contains `observation.json`, a SHA-256-verified manifest, both regression runs,
the native replay with exact DLL/component input hashes, source snapshots,
binaries and the bounded reference records. The active catalog links the new
observation separately from static dispatch coverage.

Build with `tools/wine/build_dcomp_experiment.sh` into a new lab directory. Use
the matched private Wine components listed in Chapter 4 and a disposable prefix.
The regression command is `run_dcomp_tests.py` with `--framework --composited
--embedded-child --input-binding` plus the build/prefix/log/native-D2D paths.
For native replay, use `run_overlay_trace.py --dcomp-build-dir` pointing at
`wine-lab/codex-run/dcomp-input-20261011`, an unused output directory and a fresh
marked disposable prefix. Both runners own private Xvfb displays. The original
prefix, baseline compositor DLL, desktop session and running VMs remain intact.
