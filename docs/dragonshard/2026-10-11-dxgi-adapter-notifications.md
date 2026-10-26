# Chapter 9: Adapter notifications, and using the compiler G HUB already ships

The confirmed DXGI adapter-registration blocker is cleared in the private Wine
10.20 build. The resulting native replay exposed shader function linking as the
next failure. Selecting G HUB's own bundled `d3dcompiler_47.dll` cleared that
failure too: the tray survived the full 60-second debugger observation window
without an access violation or the previous fatal exception. This is bounded
startup evidence, not full interactive tray acceptance.

## Implementation and validation

Patch `0031-dxgi-adapter-notifications.patch` implements
`IDXGIFactory7::RegisterAdaptersChangedEvent`, its unregister method, and
`IDXGIFactory1::IsCurrent`. Microsoft's
[registration contract](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_6/nf-dxgi1_6-idxgifactory7-registeradapterschangedevent)
is about adapter-enumeration changes; [IsCurrent](https://learn.microsoft.com/en-us/windows/win32/api/dxgi/nf-dxgi-idxgifactory1-iscurrent)
indicates when an adapter change requires rebuilding the factory.

Each factory stores a sorted KMT adapter-LUID snapshot. A threadpool timer polls
that snapshot once per second while registrations exist. A changed adapter set
signals the retained event handles and permanently marks the factory stale.
Source-count changes and enumeration reordering do not invalidate the factory.
Enumeration errors do not masquerade as adapter removal. A failed IsCurrent
observation returns false without permanently poisoning a previously current
factory. Registration checks the handle type and EVENT_MODIFY_STATE access,
duplicates the handle, and allocates a nonzero cookie. Unregister serializes with
signaling; destruction disarms and drains callbacks before releasing state.

This is a polling implementation because this Wine version has no adapter-change
notification primitive. A transient change that begins and ends between polls
can be missed. Physical GPU hotplug, exact Windows error-code parity, and full
WineD3D re-enumeration after a hardware change remain unvalidated. Implementing
these three API entry points is not a claim of complete DXGI conformance.

The independent MinGW COM ABI probe passes **882/882 assertions**. Its first
26 checks use the real KMT backend. The controlled-topology stage replaces only
two imports in the test process's DXGI module, leaving WineD3D and the host driver
untouched. It exercises adapter addition/removal, reordering, changing source
counts, failed enumeration, empty topology, multiple factories, event rights,
caller handle closure, invalid/foreign cookies, 256 concurrent register/unregister
operations, and destruction while a callback is blocked. All 296 synthetic KMT
adapter handles are closed. These synthetic checks must not be described as a
physical GPU hotplug test.

Original Wine DXGI fails the control run with nine failed assertions out of 27;
registration still returns E_NOTIMPL and its absent enumeration import prevents
the synthetic stage. The final patched DLL retains the existing graphics tests:
window loss **25/25**, shared textures **121/121**, completion events **505/505**.
Total measured acceptance for this pass is **1,533/1,533 assertions (100%)**.
The patch-series builder successfully recognizes and reproduces the new series.
Tests and native replays use owned Xvfb displays and disposable prefixes.

## Native progression and the more efficient shader fix

The first replay changes only DXGI among the private runtime components from
Chapter 8. The debugger is rebuilt from identical committed source (its binary
hash changes); the trace driver adds the adapter-notification debug channel.
The former 324 `80004001` reports at `displaymanager.cpp:409` become zero, and
the native registration succeeds. The resulting failure is different:
`8898008d` (`MILERR_NO_HARDWARE_DEVICE`), with a final failure score of 72.

The new error chain starts with 53 failures at dwmcorei RVA `d8890`, source line
74. That address is in a noncontiguous body belonging to `1800705c4`; a nearest
function-address search wrongly selects a tiny unrelated fragment. Querying
the exported reference table resolves the true owning function. Its decompile
shows `D3DCreateFunctionLinkingGraph`, followed by graph vtable slot `0x20`:
`ID3D11FunctionLinkingGraph::SetInputSignature`. Wine's matching method returns
E_NOTIMPL, and the runtime records 53 matching stub calls. The error propagates
through shader/device initialization into MILERR_NO_HARDWARE_DEVICE.

G HUB already ships a native shader compiler in its parent application directory.
The agent subprocess was loading that compiler, while the tray loaded builtin
Wine D3DCompiler_47. Instead of implementing the many incomplete graph and linker
methods piecemeal, a second otherwise matched replay copies the bundled DLL into
the disposable tray directory and selects it with `d3dcompiler_47=n`.
`run_overlay_trace.py --native-d3dcompiler PATH` makes that experiment repeatable,
records its SHA-256, and leaves the host installation unchanged.

Bundled compiler SHA-256:
`3ae79f9099f2295facfd891aba109358cf475acc37528df8cf9e62fde590a069`.

| Observation | Patched DXGI, Wine compiler | Patched DXGI, bundled compiler |
| --- | ---: | ---: |
| Adapter-registration failures at line 409 | 0 | 0 |
| Shader-input failures at RVA d8890 | 53 | 0 |
| Recorded composition error reports | 777 | 1 |
| Overlay-initializer returns | 6 | 34 |
| Overlay entries | 12 | 68 |
| Access violations | 0 | 0 |
| Native fatal exception before cleanup | e0434e49 | None observed |
| Committed input scenes | 2 empty | 2 empty |

The surviving run reports `armed=1 exited=0 access_violations=0`. The harness
terminates its own tray process after the fixed observation interval; this is
not a clean application exit or proof of indefinite stability. The one remaining
composition error is `80070001`, source line 955, caller RVA `197818`; it does not
lead to the previous retry/fatal chain during this run.

## Remaining scope and next action

For this pass, both identified startup blockers are cleared in the isolated
configuration: adapter registration by implementation, shader linking by using
the application's existing native component. The three DXGI methods are
implemented and all selected regression assertions pass. There is still no
successful full interactive tray acceptance test, and no defensible percentage
of all G HUB functionality can be derived from these assertion counts.

Both native replays publish only two empty composition scenes
(`shared_regions=0 bound_windows=0`). The next investigation should keep the
bundled compiler selected and trace why populated scenes are not published, then
check rendering, tray-menu interaction, and input delivery. Missing toast and
other activation messages are observations, not yet demonstrated fatal blockers.
Do not resume whole-binary export or implement each nearby stub merely because
it appears in a log. The saved corpus and a controlled component substitution
were sufficient to identify and cross this boundary.

The user's persistence in demanding reusable evidence pays off here: existing
cross-reference ownership data resolved the split native function, while the
paired replay avoided an unnecessary shader-compiler implementation campaign.

## Reproduction and evidence

Build the pinned source with `tools/wine/build_private_d3d11.sh SOURCE BUILD
--enqueue-event`. Inside the private runner with the matching graphics DLL trio,
run `tools/wine/run_graphics_tests.py --build-dir BUILD --log-dir NEW_DIRECTORY
--adapters-only`; omit `--adapters-only` for the existing graphics regressions.

The native replay uses the same composition, timer, text and storage builds as
Chapter 8. Add the following argument to its existing invocation:

```text
--native-d3dcompiler 'wine-lab/codex-run/coreapp-prefix/drive_c/Program Files/LGHUB/d3dcompiler_47.dll'
```

Evidence is archived under
`/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/dxgi-adapters-20261011/`:
both replay traces and manifests, final/control/regression logs, exact binaries,
patch/source snapshots, resolved cross-reference owner functions, an observation
JSON, and a checksum manifest. The authoritative research catalog gains a runtime
observation; binary-analysis coverage is unchanged. Root free space remained
approximately 59 GiB. No system package, running desktop, or original prefix was
modified for testing.
