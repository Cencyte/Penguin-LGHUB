# Chapter 4: Runtime proof of the null-overlay failure

## Result

Two private, headless tray executions reproduced the complete same-object
failure chain. The DirectComposition visual's LINEAR interpolation setter
returns `E_NOTIMPL`. `CLocalAppRenderTarget::EnsureUnderlyingWindowTarget`
propagates that failure before constructing either overlay context. The same
target then enters `BeginOverlayCollection` and calls the overlay method with
RCX zero, faulting at `dwmcorei.dll+0x82872`, address `0x18`.

This confirms the initialization failure and subsequent null dereference in
these runs. It does not establish a working tray or resolve the separate GL
context failure. No implementation behavior was changed in this investigation.

## Exact observed chain

The final run (`overlay-trace-20261011-v3`) recorded these events on thread
`0x3f4`, with full target object `0x7fd18018d4f0`:

| Event | dwmcorei RVA | Observation |
| --- | --- | --- |
| Initializer entry | `887f8` | Shared target handle `3d4`; visual and both overlays initially zero. |
| Interpolation call | `8888f` | Visual receiver `7fd1cfb0b790`; mode 1; slot `+58` equals RAX target. |
| Setter returned | `88894` | EAX=`80004001`; both overlay fields still zero. |
| Initializer epilogue | `88c87` | EBX=`80004001`, which the next epilogue instructions return in EAX. |
| Target Begin | `82830` | RCX=`7fd18018d5a0`, exactly full object `+b0`; overlays `+108/+110` zero. |
| Overlay Begin | `82868` | RCX=0. |
| Access violation | `82872` | Read of `18`; both first- and second-chance events captured. |

Border, backface and both overlay-construction breakpoints each had zero hits.
This matches the initializer's checked negative-HRESULT branch. The earlier
`v2` run captured the same chain with a different process/object address.
Neither run changed a setter return value, skipped instructions, nor fabricated
an overlay. Debugger single-stepping necessarily changes scheduling.

## A concrete runtime dispatch binding

At the interpolation call, receiver vtable slot `+0x58` and RAX both contained
`0x6ffff8687b70`. The mapped module was the lab `dcomp.dll` at
`0x6ffff8660000`, giving target RVA `0x27b70`.

The DLL's COFF symbol table independently identifies that RVA as
`Visual::SetBitmapInterpolationMode(DCOMPOSITION_BITMAP_INTERPOLATION_MODE)`:
preferred image base `2c1140000`, symbol `2c1167b70`.

- Caller dwmcorei SHA-256:
  `833b78602b12d8f1d81c327158021a8548accef185718eb41acf24407c647cdd`
- Callee lab dcomp SHA-256:
  `1cc7e6fff8c4a83d5187656c8fe76370f90c5b3bcd8f6ea9c76250e680dd1105`

The caller instruction directly calls a CFG dispatch thunk; RAX carries the
actual indirect destination. This observation is stored separately from static
dispatch classifications. It is one observed startup binding, not a percentage
of every possible runtime receiver. No allocation/free lifetime trace was
collected; continuity here is the adjacent initialization/Begin sequence on one
thread, corroborated by object fields and the interface adjustment.

## Earlier graphics failure remains a separate branch

The initial run armed all nine then-configured breakpoints but reached none of
the initializer sites. It logged a failed backup GL context, invalid shared
texture transfer, and fail-fast `c0000602`, preceded by error `80004005`.
The other two runs also reported GL errors but reached the overlay crash.
Do not interpret three runs as a reliable failure-frequency estimate.

The local WineD3D shared texture implementation marks a transport broken when
`context_acquire()` yields an invalid context, causing transfer failure. The GL
context's backup `wglMakeCurrent()` reports `0x7d0` (invalid pixel format).
Whether window lifetime, context ownership, or pixel-format handling caused
that failure remains unproven. Returning success after a failed texture transfer
would discard the very failure evidence we need and would not restore pixels.

## Artifacts and checks

Authoritative archive:
`/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/runtime-overlay-20261011/`

- `observation.json`: verified sequence, dispatch binding, hashes and limits.
- `manifest.json`: SHA-256 verified copies of 23 files, 3,533,732 bytes.
- Three run directories preserve setup, WNF/CoreApplication preflight, runtime
  and debugger results. The first run's debugger stdout interleaves with Wine
  output; `v2` and `v3` use a dedicated `probe.log`.
- `v3/inputs.json` (inside its full run directory name) hashes the private Wine
  components, copied DLLs, probe and harness; it records experiment flags.
- Source snapshots and the final debugger executable are included.

The active research catalog now links `observation.json`; the static ledger's
runtime-target flags remain unchanged. A separate first/second-chance pair is
one access violation, not two independently reproduced crashes.

Both preflight checks passed in all three runs. The C++ probe compiled with
`-Wall -Wextra -Werror`; Python syntax and repository whitespace checks passed.
The final evidence was checked for event order, equal object/thread identities,
receiver/slot/target equality, HRESULT propagation, null fields, interface offset,
crash address and zero hits at skipped initialization stages. Probe exit 0 means
the debugger ran successfully; the child still exited with `c0000005`.

Guard checks rejected the original lab prefix, a mismatched binary and an
existing output directory. The disposable prefix was removed after archive
verification and process cleanup; the original lab prefix remains intact.
Working-disk free space after cleanup was 59.1 GiB (Captures: 441.3 GiB).

## Reproduction

Create a stopped disposable copy of `wine-lab/codex-run/coreapp-prefix` and put
an `.overlay-trace-disposable` marker in its root. Do not point this harness at a
desktop/application prefix. The harness checks the native DLL hash and refuses
an existing output directory. The probe validates all ten breakpoint instruction
sequences before installing any. Changes affect only the disposable prefix and
process memory. The run uses a new Xvfb display and a bounded debugger lifetime.

Build the probe:

```sh
x86_64-w64-mingw32-g++ -std=c++17 -O2 -Wall -Wextra -Werror -static -municode \
  tools/wine/tray_overlay_trace.cpp \
  -o wine-lab/codex-run/overlay-trace-build/tray_overlay_trace.exe
```

Run it through `tools/wine/run_private_build.py`, passing the disposable
`--prefix` and these matched existing build directories under `wine-lab/codex-run`:

| Runner option | Build directory |
| --- | --- |
| `--build-dir` | `wine-build` |
| `--composition-build-dir` | `wine-window-build` |
| `--d3d11-build-dir`, `--dxgi-build-dir`, `--wined3d-build-dir` | `wine-d3d-probe-build` |
| `--dwrite-build-dir`, `--windows-storage-build-dir` | `wine-storage-build` |
| `--windows-ui-build-dir` | `wine-ui-build` |

The runner's trailing command is:

```sh
python3 tools/wine/run_overlay_trace.py --lab wine-lab/codex-run \
  --output wine-lab/codex-run/AN-UNUSED-RUN-NAME \
  --probe wine-lab/codex-run/overlay-trace-build/tray_overlay_trace.exe
```

## Next acceptance gates

1. Implement the required LINEAR interpolation semantics, with rendering tests.
   Then exercise SOFT border and VISIBLE backface behavior, which the static
   initializer requires next but these traces never reached.
2. Repeat this probe: initialization must create both overlays and avoid the
   null call. That result would establish only this initialization milestone.
3. Diagnose and validate GL context/shared-texture lifetime separately, including
   successful pixel transfer. It can prevent reaching the overlay milestone.
4. Validate responsive tray windows, visible content and actual interactions.
   No end-to-end tray acceptance percentage is established by these traces.

The original 21-binary/600,281-function export is complete as an evidence-export
metric. General runtime dispatch remains open; repeating a full export will not
replace these scenario-specific object and target observations.
