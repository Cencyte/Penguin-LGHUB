# DragonShard: G HUB evidence manuscript

This is the continuing research record requested by Cencyte on October 10–11,
2026. It records observations, experiments, unsuccessful attempts, and the next
questions. The machine-readable evidence remains authoritative for addresses,
binary hashes, snapshot identities, and counts.

## Scope and measurements

The completed campaign covers 21 selected binaries and 600,281 currently
identified internal functions. All have export/index records; 600,270 have
ordinary successful C output. The 11 original exceptions have verified
supplemental C or parent-function recovery. These percentages describe exported
evidence, not semantic correctness or full Wine support.

The remaining work selected for this chapter is:

1. Explain/recover 427 instruction sites absent from exported high-level p-code.
2. Recover five failed data-flow functions, covering 21 requested transfer sites.
3. Classify and improve unresolved dispatch across the entire selected corpus,
   preserving the distinction between static candidates and runtime identity.
4. Follow the `dwmcorei.dll` null-overlay crash after the evidence work permits it.

## Evidence map

- [Short retrospective: the journey to implementation](2026-10-11-journey.md)

- Active corpus catalog:
  `/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/research-catalog-final.json`
- Previous measured audit:
  `../../tools/dragonshard-2/references/final-analysis-audit-20261010.json`
- New dispatch inventory and durable supplements:
  `/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/dispatch-recovery-v1/`
- Private worker evidence and logs:
  `../../wine-lab/dragonshard-zfs-work/campaign-20261006-214743/dispatch-recovery-v1/`
- [Chapter 1: Recovering the missing dispatch evidence](2026-10-11-dispatch-recovery.md)
- [Chapter 2: Corpus-wide runtime-dispatch evidence](2026-10-11-runtime-dispatch.md)
- [Chapter 3: The next composition investigation](2026-10-11-composition-handoff.md)
- [Chapter 4: Runtime proof of the null-overlay failure](2026-10-11-overlay-runtime.md)
- [Chapter 5: Implementing the composition settings](2026-10-11-composition-sampling.md)
- [Chapter 6: Input ownership and successful overlay initialization](2026-10-11-composition-input-binding.md)
- [Chapter 7: Real input routing and GL context recovery](2026-10-11-composition-input-routing.md)

## Current result: October 11

All five originally failed data-flow functions now have successful slices for
their 21 requested sites. All 427 initially unmatched sites have an explicit
disposition: 316 processor traps, 103 recovered high-level transfers, and eight
instruction-level explanations. Eight of those original sites still lack a
matching high-level transfer; explaining their instruction semantics is not the
same metric as obtaining high-level p-code.

The final dispatch ledger classifies all 116,290 recorded sites and retains
runtime dependencies. It identifies 95,750 memory-dispatch slots, versus 63,596
previously. Runtime object identities have not been demonstrated by this static
pass. There are 74 non-trap sites without high-level slices: the eight explained
sites and 66 remaining orphan instructions. All have machine-code/context
evidence. Four other orphan sites now have successful slices and recovered C.

The authoritative new ledger is:
`/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/dispatch-supplement-v3/dispatch.sqlite`.
Its `status.json` records the final counts. The active research catalog points
to it. The 581 worker artifacts (5.9 MiB) are copied and SHA-256 verified under
`dispatch-recovery-v1/worker-archive/` on Captures; its manifest maps every original
path to the preserved copy. The original indexes and failure counts remain intact.

Original databases and failed records remain intact. Reviewed switch overrides
and fragment repairs run only on private research views. New reports must retain
remaining uncertainties; successfully exporting a slice does not establish which
object or callback is used during a real tray operation.

## Runtime follow-through

Two new headless tray traces confirm that LINEAR interpolation returns
`E_NOTIMPL`, initialization exits with both overlays null, and the same target
then dereferences a null overlay. A third trace stopped earlier on the separate
GL/shared-texture failure. One actual setter dispatch is now bound to a receiver,
vtable slot, module hash and function RVA; it is preserved as a separate runtime
observation linked from the active catalog. See Chapter 4 for the exact chain,
failed attempt, reproducible probe and remaining acceptance gates. This research
does not change composition behavior or establish healthy end-to-end tray use.

## First implementation milestone

The subsequent composition implementation passes 129/129 composited pixel
samples, including real linear filtering, soft clipping and shared-frame edges.
The native tray now returns success from all three required setters. It advances
to the previously unreached private `IDCompositionVisualRestricted::SetInput`
method, which is the next confirmed blocker. Overlay construction and complete
tray operation remain unvalidated. Chapter 5 preserves the implementation scope,
runtime progression and next method's native command format.

## Input binding milestone

Restricted visual SetInput now retains and validates real kernel sink identities,
with pending/committed lifetime tests. All eight regression executions and
133/133 composited pixel samples pass. The native initializer constructs both
overlays, returns success 31 times on one target, and enters non-null overlays
62 times without the previous access violation. Input-bound frame publication
still fails explicitly because composition hit testing/routing is not implemented.
The replay also retains the independent GL/shared-texture failure and eventually
exits with `e0434e49`. Chapter 6 distinguishes the initializer milestone from
the remaining publication, delivery and end-to-end acceptance requirements.

## Input delivery and GL recovery milestone

Chapter 7 replaces the earlier blanket publication guard with a bounded,
server-owned input scene and actual hardware delivery. Twenty routing scenarios,
including cross-process scene use and delegated pointers, pass; the full eight
composition executions retain 133/133 passing pixel samples. A controlled GL
backup-DC test reproduces the historical error code and fails before the fix;
resetting the stale pixel-format state makes all 25 recovery assertions pass.
The native tray now exposes missing ThreadPoolTimer activation. Actual interactive
tray acceptance and broader Windows input semantics remain unvalidated.

## ThreadPoolTimer and the next display contract

[Chapter 8](2026-10-11-threadpool-timer.md) implements all four timer creation
methods and passes 585/585 isolated assertions, including cancellation/completion
lifetime races. Native startup passes the old timer activation failure. Targeted
error probes and the existing corpus expose DXGI adapter-notification registration
returning E_NOTIMPL, repeated MILERR_DISPLAYSTATEINVALID, and a secondary driver
diagnostic request incorrectly reported as out-of-memory by Wine. This establishes
the next display implementation target; native nonempty input scenes and full tray
acceptance remain unverified.

## Adapter notifications and the bundled compiler

[Chapter 9](2026-10-11-dxgi-adapter-notifications.md) implements DXGI adapter-event
registration, delivery, unregister and factory-currentness; 882 notification
assertions and 651 existing graphics assertions pass. Native adapter-registration
failures disappear. The next observed failure is shader function linking, which
clears by selecting the native compiler already bundled with G HUB. The matched
headless tray survives the full 60-second observation window without its previous
fatal exception. It still publishes only empty input scenes, so the next stage
is native scene publication and interactive tray acceptance, not another complete
decompilation or a speculative shader-compiler rewrite.

## Native tray menu drawing reaches shared publication

[Chapter 10](2026-10-12-tray-menu-surfaces.md) exercises the registered tray
notification callback on private Xvfb. That exposes two drawing requirements:
RGBA8 surfaces, then a direct ID3D11Texture2D result from BeginDraw. Implementing
both clears their native error chains and produces the first observed populated
shared input scene in this test sequence. The expanded regression passes 168/168
display pixel samples. The receiving window remains on its initial empty scene:
the prototype refreshes shared imports only on a consumer Commit. Automatic
propagation to already committed consumers is the next concrete implementation
target; full tray rendering and interaction are still unvalidated.

## Shared publication reaches the receiving window

[Chapter 11](2026-10-12-shared-consumer-refresh.md) implements same-process
publication notifications on the consumer's owning thread. Retained committed
trees prevent refresh from submitting pending application mutations. The
expanded matrix passes all eight executions and 177/177 display samples,
including a relay chain, cross-thread publication, pending state isolation,
input routing, completion-event semantics and queued teardown. Native refresh
returns success and records the first populated window binding in this probe
sequence. Automatic cross-process refresh remains unimplemented. Native resize
errors and intermittent debugger stepping failures remain visible in the
archive; the next concrete application target is the private surface-resize
path reached through DCompPrivates_ResizeDCompositionSurface.

## Logical surface resizing clears the repeated drawing errors

[Chapter 12](2026-10-12-surface-resize.md) follows that wrapper to a versioned
private surface interface and implements the observed contract-10 resize slot.
Inspection corrected an initial assumption: logical-surface resize discards
content and requires full redraw, unlike public virtual-surface resize. The
expanded matrix passes eight executions and 241/241 display samples, including
64 new resize samples. A private native replay exercises successful surface
growth to 414x346 and restoration to 350x314; the repeated resize and following
invalid-argument reports disappear. One deferred 80070001 report remains. Actual
menu appearance, item actions and panel integration remain acceptance work;
successful resizing and a surviving process are not substitutes for those checks.

## Desktop handoff and the native G-Shift path

[Chapter 13](2026-10-12-desktop-agent-profiles.md) packages the tested components
into a separate, hash-checked Desktop installation. Combined testing exposed
overlapping startup ownership and an agent C-runtime crash; sequencing readiness
and letting the native UI own its tray produced a successful headless launch,
five responsive read-only API routes, two ACTIVE devices, and clean scoped
shutdown. The original crash's exact cause remains unproven. Corpus and frontend
inspection map G-Shift registration, state changes, shifted-slot resolution and
an existing SendInput backend. Physical G-Shift, native Linux output, persistence
and focus-driven profile switching are the next four acceptance targets.

## Desktop feedback repairs crash, drag release and retained-image corruption

[Chapter 14](2026-10-12-desktop-crash-drag-redraw.md) follows three failures from
actual user testing. Shcore gains the missing feature-usage export; X11 stops
restarting a mouse move after button release; retained composition activates only
for window trees that need it. A real Electron fixture reproduces the drag and
redraw failures, then passes four drags, keyboard move/resize and 32/32 display
samples. The existing tray matrix still passes 241/241 samples, with four new
base-image preservation checks. Native tray replay and the combined candidate
launch pass their bounded checks. The updated Desktop installation verifies
40 pinned files; actual user-desktop acceptance and G-Shift remain open.

## Freeze the installed version and quantify the color picker

[Chapter 15](2026-10-12-update-freeze-color-latency.md) traces an unexpected
installer dialog to the software manager's temporary-directory ACL check.
The Tested Wine launcher now disables periodic downloads/checks before startup,
blocks that manager and mounts application files read-only within its namespace.
Ten isolated checks pass while retaining the updater service. The live native
API confirms automatic downloads are off; the remaining safeguards apply next
launch. A real bundled color component produces about 19 callbacks/second;
a temporary 16 ms variant produces about 60 with the same final color. That
experiment stays out of the live application pending full-app measurements.
A user-paced Desktop recording action now captures drag timing and long tasks;
three fixture drags and native frontend attachment validate the recorder.

## Reopen a session whose window and tray have exited

[Chapter 16](2026-10-13-desktop-reopen.md) follows a Desktop launcher warning
that blocked testing even though only the background agent and updater survived.
A verified local control endpoint now forwards repeat clicks into the existing
private Wine session, reaching G HUB's native window restoration handler. An
older background-only supervisor is migrated on the user's next launch.
Twenty-two isolated native checks validate migration, window recreation,
single-instance reuse, display isolation, clean shutdown and update protection.
The recording-mode run passes 23/23 checks, including monitor reattachment.
The cause of the original UI/tray exit and full user-desktop acceptance remain
open; background process survival is no longer treated as proof of a usable UI.

## A frozen color dot despite prompt mouse input

[Chapter 17](2026-10-13-color-picker-local-state.md) follows Cencyte's correction
that the dot moved only about once every ten seconds. The earlier recorder did
not measure dot position. Inspection found local picker state overwritten by an
unchanged parent color that is published only after a pause. A controlled-picker
fixture reproduced zero dot movement over twelve seconds; the scoped state fix
produced 230 position updates with the same final color and one delayed commit.
A separately pinned ASAR is mounted only in new private Wine sessions; 27 native
launch/update/reopen checks pass. The dot-aware recorder and fix are ready for
user-desktop acceptance, while renderer stalls and physical-device behavior
remain separate validation work.

## Card highlights were delayed by buffered X11 drawing

[Chapter 18](2026-10-13-card-hover-presentation.md) starts with Cencyte's
confirmation that the color picker works and his next observation: peripheral
card borders highlight inconsistently. Native input and CSS hover were correct,
but X11 pixels lagged behind. Both Wine window-image upload paths now flush
completed drawing requests, preserving offscreen batching. The old driver passes
488/756 border samples; the repair passes 2,268/2,268 across grid, list and a
verified non-XRender fallback. Window regressions and 27 native launch/reopen
checks pass. The private Desktop driver is staged for the next full launch;
user-desktop hover acceptance remains pending.

## The tray's hover crash, placement, and still-incomplete actions

[Chapter 19](2026-10-14-tray-pointer-and-placement.md) follows user confirmation
of the picker and card-hover fixes. A missing pointer-transform export caused
the next real-desktop crash. Its fallback and a host-window placement hook now
pass 19 API, 148 geometry/lifecycle, and 75 native placement/hover/dismissal checks.
The earlier executable-layout patch was withdrawn after screenshots exposed a
blank menu despite passing geometry tests. The replacement is staged in the
Desktop launcher. Clicking an action exposes a separate pointer/gesture path:
experimental repairs reach the interaction engine, but neither Wine's partial
implementation nor the native DLL experiment completes the action test. These
experiments remain disabled and their patches/evidence are preserved. A reboot
preserved the work; Ghidra, SystemSage and file integrity were reverified.
