# Chapter 3: The next composition investigation

Follow-up: [Chapter 4](2026-10-11-overlay-runtime.md) now records two headless
runtime reproductions of the same-object failure chain below. This chapter
retains the original static hypothesis and investigation plan.

## Confirmed saved-run failure

The previous normal-adapter tray run contains an unhandled read of address `0x18`.
At the fault, RCX is zero and RIP maps to `dwmcorei.dll+0x82872`, inside
`CLiftedOverlayContext::BeginOverlayCollection` (entry `180082868`). Instruction
bytes `44 8b 41 18` read `[RCX+0x18]`. Its caller is
`CLocalAppRenderTarget::BeginOverlayCollection`, entry `180082830`, whose call at
`180082844` uses the overlay pointer loaded from interface offset `+0x58`.

The exact DLL hash is:
`833b78602b12d8f1d81c327158021a8548accef185718eb41acf24407c647cdd`.

Saved evidence is under `wine-lab/codex-run/`:

- `tray-activation-final-runtime.log:2371`: linear interpolation rejected.
- `tray-activation-final-runtime.log:2372`: exception register capture, RCX=0.
- `tray-activation-final-runtime.log:2389`: unhandled read of address 0x18.
- `tray-activation-final.log`: faulting thread in exception handling; the UI
  thread was sampled separately in wait-completion processing.

The wait stack remains an observation, not an established deadlock diagnosis.

## Strong hypothesis, still requiring a runtime trace

`CLocalAppRenderTarget::EnsureUnderlyingWindowTarget`, entry `1800887f8`, creates
a visual and requests LINEAR bitmap interpolation, SOFT border mode and VISIBLE
back-face state before creating overlay contexts at full-object offsets `+0x108`
and `+0x110`. `tools/wine/dcomp_experiment.cpp` rejects these required setters.
An error exits initialization before those objects exist.

The next focused trace should capture:

1. Initialization entry, target/overlay fields and the returned HRESULT.
2. The interpolation setter result and whether overlay creation is reached.
3. The same object's later `BeginOverlayCollection` call.
4. Whether fixing actual rendering semantics eliminates the exception and
   restores responsive tray windows.

The existing `coreapp_activation_final.py` harness was inspected. It uses Xvfb,
a private prefix and matched private Wine builds, but writes fixed historical
log filenames. A new execution must use distinct logs and preserve the existing
evidence. It also installs temporary registrations/DLLs and restores them in its
cleanup. Verify the lab prefix is stopped before using it.

This audit did **not** execute a new tray test or change Wine behavior. The
relationship between the initialization failure and null object is strongly
supported statically but not yet a traced causal chain. The independent
shared-texture GL-context error also remains to test. Do not replace required
setters with unconditional success and call the compositor finished.

## Efficient next use of DragonShard

Use the existing symbol overlay to retrieve these named functions, the new
dispatch ledger to identify their receiver/slot dependencies, and a private
runtime trace to bind those slots to actual objects. Another complete C export
would not answer the missing runtime identity question. Preserve any newly
observed target with binary hash, module-relative address, scenario, call site
and receiver lifetime evidence; label it scenario coverage rather than universal
runtime coverage.
