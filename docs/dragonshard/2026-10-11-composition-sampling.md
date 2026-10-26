# Chapter 5: Implementing the composition settings

The analysis phase was accepted as cleared by Cencyte on October 11. This is
the first implementation step following the traced initializer failure.

## Implemented behavior

The lab compositor now retains and inherits bitmap interpolation, border and
backface modes. Invalid enum values fail without modifying the previous state.
Changes are published through the existing Commit path.

Fractional translations and rectangular clip coordinates are supported. The
BGRA renderer samples nearest texels or performs bilinear interpolation of
premultiplied colour and alpha. SOFT edges use pixel-area coverage; HARD edges
use pixel-center inclusion. Fractional clips apply to a composed subtree, so
overlapping children do not each add another copy of the group's edge coverage.
Temporary layers cover only the clipped region, with a cumulative 256 MiB
scratch limit per rendered tree. Integer-aligned content keeps a direct sampling
fast path. Shared frame bounds round outward, preserving partially covered
columns at either end; imported frames use the same sampler.

Backface state is stored and inherited. The renderer's supported translations
leave surfaces facing the observer, so both VISIBLE and HIDDEN draw those front
faces. This does not implement 3D transforms, rotation, scaling, animation or
general effects. Those unsupported operations still return failure.

Public contracts used:

- [Interpolation and inheritance](https://learn.microsoft.com/en-us/windows/win32/api/dcomp/nf-dcomp-idcompositionvisual-setbitmapinterpolationmode)
- [Soft/hard borders and inheritance](https://learn.microsoft.com/en-us/windows/win32/api/dcomptypes/ne-dcomptypes-dcomposition_border_mode)
- [Backface visibility](https://learn.microsoft.com/en-us/windows/win32/api/dcomp/nf-dcomp-idcompositionvisual2-setbackfacevisibility)

The default resolved modes are nearest-neighbor sampling, hard borders and
visible backfaces. These tests establish the implemented translation/clip scope,
not bit-for-bit equivalence with every native Windows rendering case.

## Validation design

Tests exercise actual composited X-server pixels through the existing private
Xvfb/KWin harness, including the native D2D DLL and native FrameworkUdk wrappers.
The added cases distinguish nearest from bilinear sampling using four differently
coloured texels; check mode inheritance, overrides, reparenting and Commit
visibility; check soft/hard edges and overlapping/nested clips; verify negative
fractional positions and premultiplied alpha; and check fractional shared-frame
extent and imported-frame filtering.

The initial run passed the new sampling cases but failed an old test that
explicitly expected a fractional offset to be rejected. That old assumption
also moved the visual and caused subsequent pixel failures. The invalid-input
case now uses infinity, while positive fractional behavior has its own tests.
The final build also tightens clipping allocation and adds the nested-clip case.

The overlay harness accepts an explicit `--dcomp-build-dir`, preserving the
baseline DLL. The debugger now gives the child inherited standard handles so
native component diagnostics are captured alongside Wine's logs. Return values
and native object fields are never rewritten by the probe.

## Verified result

The final test runner exited 0. Clock pacing, both RS5/VB device probes, both
native FrameworkUdk wrapper probes, the main rendering suite and embedded-child
rendering all passed. The final X-server capture records contain **129/129
passing pixel samples**: 102 main samples and 27 embedded-child samples. The DLL,
tests and debugger compile with `-Wall -Wextra -Werror`.

The native tray then reached all three setters on the same target object and
each returned `S_OK`. The old `E_NOTIMPL` at interpolation is cleared. In the
observed run, neither overlay context was created yet: execution stopped at a
new private method before reaching those constructors. This is **3/3 setter
gates cleared**, not completion of native initialization or tray acceptance.

The new call path is:

1. `dwmcorei.dll` initializer calls `Microsoft.InputStateManager!AttachSystemInputSink`
   through IAT RVA `1d6980` after creating/adding its first child visual.
2. InputStateManager `InputSinkInfrastructure::AttachSystemInputSink`, RVA
   `21990`, resolves an association and passes its value to helper RVA `18ff0`.
3. QI helper RVA `19064` requests `{2c4eef28-1bc0-4736-b7dd-b62692f9bd67}`;
   those bytes were checked at InputStateManager RVA `4d650`.
4. The helper calls interface offset `118`, slot **35**. Our explicit missing
   private-method handler raises `c0000002` there.

The matching Windows dcomp reference vtable at RVA `19dc20` has slot 35 at
`19dd38`, pointing to `1e60`. The verified PDB overlay identifies it as
`IDCompositionVisualRestricted::SetInput(void*)`. Its implementation delegates
to `CDevice::VisualSetInputSink`, RVA `3520`, which queues a 16-byte command:
opcode `0x16`, visual resource ID, and the opaque input-sink value. That is a real
association operation, not merely a notification that can be ignored.

The next implementation must establish the argument's handle/identity and
lifetime rules, connect it to the existing retained input-sink support with
appropriate commit semantics, and validate input delivery. A constant `S_OK`
would lose this behavior. The independent GL-context/shared-texture error also
appeared in the new run and remains open. No successful overlay constructor,
responsive tray or complete interaction test is claimed.

## Preserved evidence

Archive:
`/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/composition-sampling-20261011/`

- `manifest.json`: 41 SHA-256 verified artifacts, 7,384,137 bytes.
- `observation.json`: pixel counts, the three successful native returns and
  next blocker, linked from the active research catalog.
- `dcomp-sampling-final-tests-20261011/`: final regression logs, including
  actual composited X-server samples.
- `composition-overlay-20261011/`: exact runtime inputs, preflight logs,
  native-component output and separate debugger events.
- `next-blocker/`: six bounded corpus function records for the call chain.
- `source/` and `dcomp-sampling-final-20261011/`: source snapshot and binaries.

The new DLL is also retained under
`wine-lab/codex-run/dcomp-sampling-final-20261011/dcomp.dll`. The preserved baseline
DLL remains available for comparison. Replay uses the Chapter 4 runner and
`--dcomp-build-dir wine-lab/codex-run/dcomp-sampling-final-20261011`, with a fresh
disposable prefix and unused output directory.
