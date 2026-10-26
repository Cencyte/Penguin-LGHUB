# Chapter 11: Deliver publication to the committed consumer

The same-process shared-consumer refresh gap is implemented in the isolated
DirectComposition backend. A native tray publication now reaches the window's
owning thread and binds a nonempty input scene without another application
Commit. This closes one specific handoff gap; it does not establish full tray
rendering or interaction.

## What the first attempt revealed

The initial implementation called a consumer's ordinary Commit directly from
the publisher. Same-thread regressions passed, but the native tray returned
`8001010e` (RPC_E_WRONG_THREAD). The publishing render thread and the receiving
window thread are different. The trace overturned the initial assumption that
they shared an apartment.

Posting back to the consumer thread cleared that failure, but calling Commit
still had a semantic flaw: it could submit application edits that had not been
committed. We therefore retained a private rendering copy of each successfully
committed target tree. It contains layout, child order, clipping, sampling modes,
local surface pixels, input bindings and shared-resource identities. These
copies contain no references back to the device and are never exposed through
application COM interfaces. Automatic refresh samples shared resources within
that accepted tree. Pending root changes, removed/released original visuals,
new local EndDraw pixels and layout edits remain pending until explicit Commit.

## Dispatch, ownership and propagation

Each device has an invisible message-only window on its creating thread.
Publication queues coalesced notifications; it never waits for another
device's mutex or invokes another apartment synchronously. The receiver resolves
its endpoint against a weak registry protected by the same lock as final
Release. Queued messages contain neither a raw object pointer nor a strong
reference that could leak when a thread stops pumping. Windows retain the
DLL's unload count until destruction, including off-thread final Release.

Refresh does not advance application batch IDs or signal the application's
commit-completion event. Newly created, uncommitted targets are excluded.
Relay targets suppress unchanged pixel/input publications, allowing ordinary
acyclic chains to settle instead of repeatedly notifying one another.

Explicit commits still sample cross-process shared sections. **Automatic
cross-process notification is not implemented in this patch.** The native tray
case observed here uses two devices in one process. Threads must pump their
message queues for automatic delivery. Arbitrary cyclic resource graphs,
multi-thread window targets on one device, GPU fences and atomic multi-resource
transactions are not claimed. Snapshot capture has a 256 MiB per-submission
budget; this is a bounded CPU compositor, not the complete Windows compositor.

## Validation and remaining errors

The expanded regression passes all eight selected executions and **177/177
final-display pixel samples (100% of this regression set)**. The new checks cover:

- producer-only pixel and input refresh;
- publication from another thread without consumer message pumping during the
  publication itself, followed by asynchronous delivery;
- a producer-to-relay-to-window chain;
- pending consumer layout, local surface pixels, root detachment and child
  removal/release remaining isolated until explicit Commit;
- pending clipping remaining isolated for real routed input;
- unchanged consumer batch IDs/completion event during automatic refresh;
- subscriber destruction with notifications queued, off-thread final Release,
  and DLL unload lifetime.

The real tray traces record two successful consumer-thread refreshes. The later
one reports `shared_regions=1 bound_windows=1`, where Chapter 10 reported zero
populated window bindings. The initial empty refresh is expected. These are
runtime observations, not merely synthetic regression outcomes.

The trace is **not clean overall**. A completed retained-scene replay records
313 composition error reports: one `80070001`, 156 `80004002`, and 156
`80070057`. Several other replays stop early on the debugger harness's
`non-overlapping single step` assertion, after successful window refresh. Those
failed attempts are preserved, not counted as successful full observations.
The assertion's printed winerror is incidental last-error state, not evidence
that sudo or MCP access was lost. These runs have different observation lengths,
so raw error totals must not be treated as comparable failure rates.

The first repeated E_NOINTERFACE report is dwmcorei RVA `1a8405`, source line
105. Reference ownership in the saved corpus identifies function `1801a8378`.
Its code checks stored dimensions, calls **DCompPrivates_ResizeDCompositionSurface**,
and reports the returned failure at that line. The bundled FrameworkUdk export
is at `180075df0`; it dispatches through its selected platform table at offset
`0x18`. The next targeted investigation is that platform implementation's
surface-resize interface and behavior. The later invalid-argument chain must
be retested after resize support; its causal relationship is not yet proven.

No additional corpus-wide decompilation was needed. Export/research coverage
remains 21 selected binaries and 600,281 identified function records. Neither
that coverage nor 177 passing pixel checks measures whole-application support.
Full menu appearance, repeated open/close, item actions, Linux panel delivery
and hardware-backed G HUB behavior remain separate acceptance gates. There is
no measured whole-program implementation denominator from which to report a
defensible overall completion percentage.

A correction to the earlier conversational explanation: a new native Linux tray
bridge is not known to be necessary. The local Wine 10.20 X11 driver already
sends `_NET_SYSTEM_TRAY_OPCODE` docking requests (`winex11.drv/window.c`), and
this machine has `/usr/bin/xembedsniproxy` with its Plasma user service and
autostart entry. Validate that existing Wine/Plasma path before proposing an
additional bridge. Presence of those components alone is not an end-to-end
panel acceptance test.

## Evidence and reproduction

Build with `tools/wine/build_dcomp_experiment.sh`. The private runner executes
`run_dcomp_tests.py` with native D2D, FrameworkUdk, composited capture,
embedded-child and input-binding options. Native replays use
`run_overlay_trace.py --exercise-tray` with the bundled shader compiler and
the checksum-identical ZFS copy of native D2D. Exact argv, DLL hashes, sources,
test logs, successful and failed native attempts, and the next-error corpus
extracts are retained under:

`/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/shared-consumer-refresh-20261012/`

`observation.json` and the SHA-256 manifest distinguish test phases and their
actual outcomes. No installed Wine files, packages, live launchers or desktop
windows were changed. SystemSage responded before the work; roughly 56 GiB
remained on the working ZFS filesystem and 442 GiB on Captures before archiving.

The useful milestone is concrete: committed data now crosses the render-thread
to window-thread boundary with its input scene intact. The next work follows
an observed surface-resize failure, rather than treating a surviving tray
process as proof that its menu is finished.
