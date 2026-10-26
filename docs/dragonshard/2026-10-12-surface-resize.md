# Chapter 12: A logical surface can change size

The private resize path used by the bundled FrameworkUdk is implemented in the
isolated compositor. All eight selected regression executions pass, including
**241/241 final-display samples**, of which **64 are new resize samples**. A native
tray replay records successful surface growth to `414x346` and restoration to
`350x314`. The repeated resize `80004002` and subsequent `80070057` reports from
Chapter 11 are absent in this observation. One earlier `80070001` report remains.
These results close the observed resize blocker, not all tray acceptance gates.

## Finding the interface and avoiding the wrong contract

The saved corpus supplies the complete dispatch chain without another export:

1. dwmcorei `1801a8378` compares dimensions and calls
   `DCompPrivates_ResizeDCompositionSurface`; its failure report at RVA `1a8405`
   supplied the previous diagnostic.
2. FrameworkUdk export `180075df0` dispatches through the fallback table at
   RVA `96de8`, slot 3, to `18001ec80`.
3. That wrapper selects platform vtable slot 19. The switcher at `180015e70`
   queries UniversalApiContract versions, independently of Wine's Windows-version
   setting. This Wine environment reports contract 10.
4. The corresponding implementation, `180031e00`, calls `180028350`, which queries
   IID `5680c979-14c8-48b5-8b92-3247379fd907`. Thunk `18002b440` then invokes surface
   vtable slot 10 with width and height.

Contracts 12 and later instead select `180031e40` / `18002b420`, invoking slot 11
under the **same IID**. The newer native dcomp reference confirms that its slot 10
is a different method taking an output pointer. Consequently this patch supports
the observed contract-10 layout only; it does not alias both slots to resize.
Changing advertised WinRT contract versions requires revisiting this interface.
Unused private slots retain the experiment's explicit unsupported-method trap.

The initial idea of preserving the overlapping pixels was corrected during
research. Microsoft's public
[virtual-surface Resize](https://learn.microsoft.com/en-us/windows/win32/api/dcomp/nf-dcomp-idcompositionvirtualsurface-resize)
preserves existing content within the new bounds. This private operation acts on
a **logical surface**, whose native reference behavior differs. Native dcomp
`180060920` validates draw state and paired dimensions; `180060ab4` replaces the
backing when necessary and drops current content/validity even when dimensions
match. `180065a14` requires the first subsequent draw to cover the full surface.
Those routines and the raw vtable/GUID bytes are retained as evidence. The
semantics are based on that inspected reference and the bundled caller; no new
Windows-side differential execution was performed.

## Implementation and regression scope

The surface partner forwards IUnknown identity and ownership to the public
surface. Its inherited drawing methods forward to the existing implementation.
Resize shares the device mutex with drawing and submission. It rejects an active
draw on the resized surface with `88980801`, rejects one-zero dimensions and
dimensions above the backend's existing 16384 limit, and accepts an empty `0x0`
surface. BeginDraw on that empty surface returns `8000000e` until a nonempty size
is restored. This does not implement public virtual surfaces.

Replacement storage is allocated before dimensions or content are changed, so
allocation failure returns E_OUTOFMEMORY without partially changing the surface.
Allocation failure was not fault-injected; rejected arguments and active draws
were tested for unchanged state. Successful resize invalidates pending content
and requires a full redraw. The previously committed image remains visible until
the producer commits. Subsequent publication reaches the shared consumer through
the existing owner-thread refresh mechanism.

The new tests exercise BGRA8 and RGBA8 with premultiplied and ignored alpha,
growth, shrink, equal-size invalidation, zero-size clearing/recovery, first-full
and subsequent partial updates, row dimensions, COM identity and retained partner
lifetime. They retain an old update texture across resize, verify its dimensions
remain unchanged, and verify later writes through it do not alter replacement
content. Another surface can be drawing while this one resizes. Invalid requests
leave an in-flight draw usable. Display samples are read from private KWin's
composited output on Xvfb. All calls use the actual FrameworkUdk export when that
library is selected, including the verified matrix here.

The initial regression attempt left six completed logs but no retained overall
exit status after a session continuation. It is preserved as partial evidence.
The subsequent complete matrix records eight exit-zero executions and all 241
samples passing. Existing input-routing, embedded-child, shared-refresh, device
and clock checks remain included. The earlier conversational count of 68 new
resize samples was incorrect; the completed logs establish 64.

## Native replay and the remaining gate

The first ordinary tray replay completed with one composition report but recorded
no resize call, so it was not treated as proof of the new path. A new optional
`--exercise-resize` mode in the SHA-pinned debugger opens the menu through its
registered notification callback, then queues normal window growth/restoration
on that newly launched private-display process. It checks process ownership
before changing its window. It neither changes application return values nor
touches a live desktop window.

That replay records four calls to ResizeSurface, including the requested
`414x346` and subsequent `350x314`, both with `hr=00000000`. The application
restores its preferred window size before the next periodic inventory, so the
inventory alone would miss the transient growth; the surface trace captures it.
One earlier resize line is heavily interleaved with Wine diagnostics and is not
used as a standalone HRESULT assertion. The consumer refresh still records a
populated input binding. No access violation or debugger stepping assertion was
observed, and the full bounded replay completed with probe exit zero.

The single remaining composition report is `80070001`, source line 955, observed
through dwmcorei RVA `197818`. Its containing function `1801977f0` is a deferred
error-reporting wrapper; its address alone does not identify the failing
operation. Its effect on final appearance remains undetermined. The earlier
repeated resize/invalid-argument chains are gone, but debugger completion does
not assert correct menu pixels, item actions, or Linux panel integration.

Research export coverage remains 21 selected binaries / 600,281 identified
function records. Resize implementation is complete for the observed private ABI
and the tested behaviors above; regression coverage is 100% of the selected
241 samples and eight executions. Neither metric establishes a whole-application
completion percentage. The next efficient step is an acceptance-oriented check
of actual menu appearance and input, while tracing the remaining deferred error
if it affects that behavior. Automatic cross-process refresh, newer private ABI
layouts, broader composition features and hardware-backed G HUB operation remain
outside this patch.

## Reproduction and evidence

Build using `tools/wine/build_dcomp_experiment.sh`. The full matrix uses
`run_private_build.py` with the previously matched component builds and
`run_dcomp_tests.py --framework --composited --embedded-child --input-binding`,
including the saved native D2D copy. The debugger is compiled with the existing
MinGW compiler and run by `run_overlay_trace.py --exercise-resize` in its stopped,
disposable prefix. Exact argument arrays, source snapshots, binary hashes,
corpus extracts, partial and complete logs, observation summary and checksum
manifest are archived under:

`/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/surface-resize-20261012/`

SystemSage and Ghidra responded during preparation. About 56 GiB remained on ZFS
and 442 GiB on Captures before archiving. No installed Wine files, packages or
live launchers were changed.
