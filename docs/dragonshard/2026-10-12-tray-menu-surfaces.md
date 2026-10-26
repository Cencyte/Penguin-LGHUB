# Chapter 10: Exercise the menu, then cross the drawing boundary

Two confirmed menu-drawing failures are cleared in the isolated compositor:
RGBA8 surface creation and `BeginDraw` returning an `ID3D11Texture2D`. The native
tray now publishes a shared scene containing one input region after its
right-click callback. The receiving window still has no populated input binding.
This is progress through native menu rendering, not full tray acceptance.

## The missing experiment

Chapter 9's surviving idle tray had an empty scene. That alone was insufficient
evidence of a rendering failure: its menu window was hidden. We needed to exercise
the notification callback before diagnosing the absence of menu content.

The saved tray corpus identifies `Shell_NotifyIconW(NIM_ADD)` at RVA `88c21`,
its return at `88c27`, and the native subclass callback at `886e0`.
`NOTIFYICONDATAW` uses callback message `0x500`, icon ID `532175291`, and the
original notification ABI. This pinned executable does not call NIM_SETVERSION.
Microsoft's [notification structure contract](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/ns-shellapi-notifyicondataw)
documents the legacy icon-ID/message layout.

`tray_overlay_trace.exe --exercise-tray` now records the actual registration,
checks success and target PID ownership, inventories only the owned tray's
windows, and posts the registered right-button down/up pair. Both callbacks
reach native code. All this happens on the runner's private Xvfb display, with
an explicit lab environment guard. No live desktop input, HRESULT, or native
object data is changed. Breakpoints validate original instruction bytes; the
runner additionally pins the tray and dwmcorei hashes. The fixed observation
window is a machine test, not a deadline imposed on the user.

## First failure: RGBA8 surfaces

Opening the menu requests a **350 x 46** surface, format **28**
(`DXGI_FORMAT_R8G8B8A8_UNORM`), alpha **1** (premultiplied). The previous backend
accepted only BGRA8. The failure appears at dwmcorei RVA `d6584`, source line 62,
owned by function `180066aa0`, which calls surface-factory `CreateSurface`.
As in Chapter 9, the exported reference table identifies split function ownership;
nearest-entry-address searches are not sufficient.

The surface now retains its requested format and creates update textures in that
format. Native Direct2D receives the same format. `EndDraw` respects mapped row
pitch and converts RGBA to the retained BGRA frame representation, preserving
premultiplied alpha and updating only the requested rectangle. IGNORE and
UNSPECIFIED alpha modes remain opaque after initialization. Uninitialized surfaces
remain transparent. Shared consumers receive canonical BGRA without a second swap.

Microsoft [documents three public surface formats](https://learn.microsoft.com/en-us/windows/win32/api/dcomp/nf-dcomp-idcompositiondevice-createsurface).
This backend now implements two of those three: BGRA8 and RGBA8. Float16 remains
explicitly unsupported; this pass does not claim HDR or full format conformance.

## Second failure: return the texture itself

The next replay passes surface creation but reports `80004002` (E_NOINTERFACE)
at RVA `cce82`, source line 138. Its owner, `18004a710`, calls surface `BeginDraw`
with the IID at dwmcorei RVA `1da6f0`, then invokes vtable slot `0x50` to inspect
the returned texture description. The binary bytes decode to
`6f15aaf2-d208-4e89-9ab4-489535d34f9c`: `ID3D11Texture2D`.

The prototype had allowed only IDXGISurface and ID2D1DeviceContext. It now also
returns the real update texture through QueryInterface, retaining normal COM
ownership and the existing drawing lifetime. This is an observed native/private
compatibility requirement, not an assertion that the public BeginDraw documentation
lists this interface. Unknown requested interfaces still fail explicitly.

## Measured results

| Observation in the bounded native replay | Format diagnostic baseline | RGBA8 only | RGBA8 + texture interface |
| --- | ---: | ---: | ---: |
| Surface-creation failures, RVA d6584 | 11 | 0 | 0 |
| BeginDraw interface failures, RVA cce82 | 0 (not reached) | 12 | 0 |
| Recorded composition error reports | 78 | 73 | 1 |
| Maximum published shared input regions | 0 | 0 | 1 |
| Populated window input bindings | 0 | 0 | 0 |
| Access violations | 0 | 0 | 0 |
| Fatal exceptions during observation | 0 | 0 | 0 |

Each replay registers the icon and delivers both callbacks. The final tray remains
alive until the harness terminates its owned process after 60 seconds. The single
remaining error is the pre-existing `80070001` at source line 955; it does not
produce the former retry chain in this window. Counts describe these particular
instrumented runs, not rates or guarantees of indefinite stability. An earlier
uninstrumented-format baseline did reach the fatal retry threshold; it is retained
as separate evidence.

The regression adds 35 final-display pixel samples: both byte layouts with all
three accepted alpha modes, direct texture and DXGI-surface access, partial
updates, pre-commit isolation, native Direct2D surface-factory lifetime, and
shared-frame publication. The prior 133 pixel checks are retained. Both the RGBA
build and the final texture-interface build pass **168/168 pixel samples (100%
of this regression set)** and all eight selected test executions. Input routing,
embedded child presentation, private ABI wrappers, device batches, and clock
regressions also pass. This percentage does not measure all G HUB functionality.

## The next implementation target

The final trace shows the window-owning device committing its initial empty
graph, followed by a different rendering device committing the menu with
`shared_regions=1`. There is no later commit from the window-owning device.
Reading the prototype confirms that imported shared frames are sampled only
inside the consumer's own `Device::Commit`. Producer publication has no mechanism
to refresh an already committed consumer graph automatically.

The next target is **producer-driven shared-consumer refresh**, with presentation
on the window's owning thread. It must keep pending application mutations separate
from committed state, propagate pixel and input changes together, retain proper
resource lifetime, and handle cross-process resources without requiring another
application Commit. Tests must cover producer-only updates, unchanged consumer
graphs, chains, teardown, and actual window/input delivery. The runtime evidence
and existing source establish this as a concrete gap; whether clearing it produces
a complete menu remains an untested prediction. Confidence in the gap is high;
confidence that it is the last tray blocker is low.

A private-display screenshot shows the separately spawned G HUB loading window
covering the display. It is retained for context, not counted as evidence of tray
menu pixels. The native tray HWNDs become visible at 350 x 46, partly outside the
screen because the private cursor starts at the origin. Full visible menu contents,
item actions, repeated open/close, and hardware-backed end-to-end operation remain
unvalidated. Binary-analysis coverage is unchanged: the saved corpus sufficed.

## Evidence, reproducibility, and the storage interruption

The build uses `tools/wine/build_dcomp_experiment.sh`. The matched private runner
invokes `run_dcomp_tests.py` with native Direct2D, FrameworkUdk, composited capture,
embedded-child and input-binding checks. Native replays use `run_overlay_trace.py`
with `--exercise-tray`, `--native-d3dcompiler`, and the selected compositor build.
Exact argv, input manifests, binary hashes, logs, corpus extracts, source snapshots,
and observation JSON are archived under:

`/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/tray-menu-surfaces-20261012/`

During testing, scheduled fstrim blocked the Windows NTFS-3G daemon in
`blkdev_issue_discard` / `submit_bio_wait`. A test stalled before launching Wine
while accessing its native Direct2D DLL. Sudo and MCP access remained available.
The owned stalled test and diagnostic were stopped; no mount or system service
was killed. The new `--native-d2d` argument permits the checksum-identical saved
ZFS copy, SHA-256
`d1425b33d6816a03769f2d635bfee7abb6392c267abbca4f5bd27593841a39fc`.
The change avoids an incidental storage dependency without changing the test DLL.
The stalled attempt is not application evidence. Working space remained about
59 GiB on ZFS and 442 GiB on Captures before archiving; no package or live launcher
was changed.

The useful shift this pass is from waiting for startup to exercising one real
behavior with an observable boundary. The user's persistence in retaining a deep,
searchable corpus made both new failures small, evidence-backed implementation
tasks. It also makes the remaining consumer-refresh gap possible to name precisely.
