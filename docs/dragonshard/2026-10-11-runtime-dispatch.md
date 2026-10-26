# Chapter 2: Corpus-wide runtime-dispatch evidence

## What the new pass does

All 237 saved indirect-evidence shards were processed with 42 Python workers.
The pass adds promoted-global storage handling, recognizes zero-offset dispatch
slots, and records each target expression's input registers, global storage,
call-result dependencies, and p-code operations. It retains finite static
candidates without claiming that a file-memory value is the current runtime value.

The final ledger overlays the recovered slices and instruction-level findings
from Chapter 1. It is a separate SQLite database, not an overwrite of any primary
function corpus. Duplicate high-p-code transfers at one instruction remain
separate evidence records while site counts use `(binary SHA-256, address)` once.

## Measured result

| Site classification | Count |
| --- | ---: |
| Memory-dispatch slot | 95,750 |
| Processor trap | 12,959 |
| Global runtime storage | 3,907 |
| Register/argument-dependent dispatch | 2,311 |
| Depends on another call's result | 851 |
| Other runtime data flow | 447 |
| Static target candidate | 59 |
| Named import identity | 6 |
| **Total recorded sites** | **116,290** |

The 12,959 traps comprise 12,150 breakpoints, 693 fast-fails, 115 other software
interrupts and one debug exception. Therefore the recorded **actual call/jump
denominator is 103,331**, not 116,290. Slot evidence covers 95,750/103,331
(92.66%) of those sites. A slot can be a vtable field or another function-pointer
table; the offset alone does not establish a C++ class or interface.

Previous static evidence identified 63,596 slots. Zero-offset handling and the
additional recoveries expose 32,154 more. This is improved target-computation
evidence, not 32,154 proven function destinations.

**No runtime target bindings were demonstrated by this static pass.** The ledger
keeps `runtime_target_proven=0`. To establish bindings, execution must observe
the receiver/table/callback value in a defined application scenario and map the
result to the exact loaded module. A single observed target is not exhaustive
proof for every profile, device, input, thread interleaving or failure path.

## Orphan instructions: a real discovery gap

The previous gap inventory had 10,695 computed-transfer sites without function
owners. Most are traps. **70 are real call/jump instructions.** Their instruction
context, incoming references and bytes have now been captured and verified.

Windows unwind metadata identified four sites in two agent regions:

- `1429b3300..1429b33fa`: a previously unrecognized independent function. A private,
  byte-checked function definition yields ordinary C (1,398 characters) and slices
  for both indirect calls, `1429b3398` and `1429b33b4`.
- `142373d63..14237449f`: a chained unwind fragment referring to parent
  `142373c60`. Merely extending the parent's listing body did not recover its two
  call sites in the decompiler. Exporting the fragment as a private analysis unit
  with incoming machine state yielded ordinary C (13,020 characters) and slices
  for `142373e70` and `14237428a`. It is **not claimed to be an independent ABI
  function**. The unsuccessful parent-extension experiment remains archived.

These results are in `recovered-regions.json`. The original 600,281-row corpus is
unchanged. Its function denominator was not proof that discovery was exhaustive;
the independent function above is concrete additional evidence of that limitation.

The remaining 66 orphan sites have low-level/context evidence and a dispatch
classification. Most are short dispatch jumps; eight use writable global pointer
cells. Their proper function ownership and full high-level slices remain open.
Together with Chapter 1's eight cases, **74 non-trap sites still lack high-level
slices**, though all have an explicit instruction-level record.

## Retrieval and provenance

Database:
`/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/dispatch-supplement-v3/dispatch.sqlite`

- `programs`: binary name, hash, path and source snapshot.
- `sites`: one row per original computed-transfer site, owner, classification,
  original instruction, bytes, high-level-slice availability and recovery source.
- `evidence`: full target candidates/dependencies and source paths. Multiple
  records per site preserve ambiguity and alternative observations.
- `sources`: source-file hashes inherited from the baseline ledger.
- `provenance.json`: hashes of the supplemental raw recovery sources.
- `worker-archive/manifest.json`: verified copies of the source worker reports/logs
  on Captures, including unsuccessful alternate-style observations.

Example query for the next composition investigation:

```sql
SELECT p.name, s.address, s.function_address, s.instruction, s.classification
FROM sites AS s JOIN programs AS p ON p.sha256=s.sha256
WHERE p.name='dwmcorei.dll' AND s.trap IS NULL
ORDER BY s.address;
```

Validation: 98 Python tests pass, including byte-aware trap classification,
RIP-relative addressing, promoted import cells, writable-memory uncertainty,
zero-offset slots, duplicate evidence versus unique-site denominators, and the
existing scheduler/index regressions. Actual headless jobs validated the Java
changes. SQLite checks report `ok`; 581 archived worker artifacts were SHA-256
verified. No source package upgrades or live desktop testing were performed.
