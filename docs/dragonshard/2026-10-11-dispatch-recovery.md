# Chapter 1: Recovering the missing dispatch evidence

## First pass

The original 427 unmatched sites occur in 303 functions across 16 binaries.
SystemSage was verified with actual CPU, RAM and disk MCP calls before launch.
Approximately 45 GiB of memory was available; the working filesystem had 58 GiB
free, and Captures had 442 GiB free. The run retained the disk guards and ran
independent headless JVMs with private project copies or private packed-database
views. It did not modify the live Ghidra project or desktop.

All 54 focused jobs completed successfully. The resource-admitted peak was 19
workers, under a ceiling of 42. The run was bounded by available memory and
outstanding worker memory commitments, not an interactive Ghidra database lock.

### Five failed data-flow functions recovered

| Binary | Original function | Recovery | Sites exported |
| --- | --- | --- | ---: |
| Microsoft.ui.xaml.dll | `18003c283` | Reviewed reunion with parent `18003be90` | 14 |
| lghub_agent_saved_wine.exe | `140ee6b90` | Reviewed image-relative switches | 1 |
| lghub_agent_saved_wine.exe | `1411501d0` | Reviewed image-relative switches | 1 |
| lghub_agent_saved_wine.exe | `141576e20` | Reviewed image-relative switches | 1 |
| lghub_agent_saved_wine.exe | `141b9abf0` | Reviewed switches plus normalization | 4 |

Coverage is **5/5 functions and 21/21 requested sites**. The original ordinary
export failures remain recorded. The last function exposes only two of its four
requested sites in ordinary optimized output; normalization retains all four.
Recovered data flow does not by itself establish all eventual runtime targets.

### The 427-site count mixed different instruction classes

- 307 sites are `INT3` breakpoint instructions.
- Nine sites are `INT 0x29` fast-fail instructions.
- Six sites gained a matching transfer in normalization.
- 105 sites remain genuine call/jump instructions absent from both ordinary and
  normalized output: 98 calls and seven jumps.

The original gap exporter uses Ghidra's computed-flow classification. Its x86
p-code models software interrupts with a computed transfer, so the 316 interrupt
sites entered the same list as dynamic calls. Their instruction bytes and raw
p-code are now preserved. They are not 316 unknown C++ virtual-call targets.

Example: `CoreMessagingXP.dll!__except_validate_context_record`, entry
`180004428`, site `18000445c`, bytes `cd29`, is `INT 0x29`. Both decompiler styles
omit a matching high-level transfer. Its raw p-code retains the interrupt and
the computed control transfer explicitly.

## Next experiment

Retry the 105 remaining call/jump sites with earlier decompiler processing and
unreachable-block elimination disabled. Preserve both previous attempts and
instruction-level evidence. Do not classify these sites as dead/unreachable
solely because optimized output omitted them.

## Reproduction and evidence

The runner is `tools/dragonshard-2/scripts/dispatch_recovery.py`. Inventory and
source catalog are under the Captures dispatch directory linked by the manuscript.
The first pass is under the ZFS `dispatch-recovery-v1` directory. Each job has
its request, source identity, instruction bytes, raw p-code, optimized slice,
normalized slice, completion marker and log. Recovery jobs additionally retain
the byte-checked switch/fragment preparations and recovered C.

This chapter is updated as results arrive; later measurements supersede the
intermediate counts only when their evidence is retained.

## Follow-up results

Each pass below targeted the same 105 remaining sites in 65 functions, with
14 independent jobs. Earlier outputs remain preserved.

| Recovery configuration | Functions whose alternate pass completed | Matching transfer sites |
| --- | ---: | ---: |
| `firstpass`, retain unreachable blocks | 53/65 | 68/105 |
| `register`, retain unreachable blocks | 54/65 | 69/105 |
| `normalize`, retain unreachable blocks | 65/65 | 97/105 |

The first two experiments encountered `Free varnode has multiple descendants`
errors. Those errors belong to the alternate styles, not new failures of the
original C exports. Normalization with unreachable-block elimination disabled
produced the best result. Retention exposes the dependency graph but does not
prove a block is executable under normal application inputs.

Combined with the six first-pass recoveries, 103 of the original sites now have
matching high-level transfers. The 316 processor traps remain classified
separately. Microsoft's [fast-fail documentation](https://learn.microsoft.com/en-us/cpp/intrinsics/fastfail?view=msvc-170)
confirms the x64 `INT 0x29` mechanism; the local instruction bytes establish which
sites use it.

## The final eight sites

| Binary | Site | Instruction-level result |
| --- | --- | --- |
| Microsoft.InputStateManager.dll | `18002f794` | Import: `marshal!LegacyMarshalerCloseCompositionInputSink` |
| Microsoft.InputStateManager.dll | `18002d548` | Import: `KERNEL32!ReleaseSRWLockExclusive` |
| Microsoft.InputStateManager.dll | `180010179` | Import: `api-ms-win-core-synch-l1-2-0!WaitOnAddress` |
| Microsoft.InputStateManager.dll | `18002f7ce` | Import: `KERNEL32!ReleaseSRWLockShared` |
| Microsoft.UI.Input.dll | `180080458` | Import: `marshal!LegacyMarshalerCloseCompositionInputSink` |
| Microsoft.UI.Input.dll | `18007e546` | Import: `api-ms-win-core-synch-l1-1-0!ReleaseSRWLockExclusive` |
| Microsoft.ui.xaml.dll | `1803b72d4` | CFG dispatcher receives a target loaded from receiver slot `0x10` |
| d2d1.dll | `1803018f3` | Bounded image-relative switch; all 12 table entries select `18011191b` |

Import identities were checked against the PE import table and RIP-relative
instruction bytes. They are symbol identities, not assertions of a particular
loaded module address or API-set implementation.

For XAML, the PE load-configuration dispatch pointer equals `1809f7188`.
Instructions load `RAX=[RCX]`, then `RAX=[RAX+0x10]`, before the call through that
pointer. The receiver remains runtime-dependent. The primary exported C also
records Ghidra's `guard_dispatch_icall` injection. Microsoft's explanation of
[the x64 guard-dispatch calling convention](https://devblogs.microsoft.com/oldnewthing/20260710-00/?p=112514)
supports interpreting RAX as the checked target, not the guard helper itself.

For Direct2D, bytes at `1801118fe` load the image base into RDX. The branch at
`180111915` reaches `1803018e1`; an unsigned bound check admits EBX values 0..11.
All 12 immutable RVA entries at `180301932` select `18011191b`. This cold block is
listed under `1803018ca`, but its incoming branch belongs to `180111780`. The
wrong listing ownership explains why asking the former function for that site's
high-level p-code did not recover it. No live database ownership was rewritten.

These eight explanations are retained in `dispatch-supplement-v3/manual-dispatch.json`,
with byte-verified context in `raw-contexts.json`. The high-level omissions remain
explicit; all 427 cases are accounted for without pretending every case gained
high-level p-code.
