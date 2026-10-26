---
name: dragonshard-2
description: >
  Saturate a many-core workstation with a parallel Ghidra decompile army: independent
  headless JVMs on reflink clones of an analyzed project, deterministic function shards,
  persistent C/symbol/reference evidence, and SQLite FTS/call-reference retrieval.
  Use for full binary/corpus exports and repeated cross-binary investigations, especially
  100K+ functions; not for a single routine already available through interactive Ghidra.
  Triggers include DragonShard, full-corpus export, and decompile the whole binary.
license: personal
metadata:
  author: Cencyte + Claude; DragonShard-2 upgrades with Codex
  born: 2026-07-31 (CLO3D op, Chapter Two)
---

# DragonShard-2 — parallel headless Ghidra decompile army

## Why (the core insight)
**The original operation hit a ~2-core ceiling in one Ghidra JVM, despite spawning many threads**:
native `decompile` subprocesses serialized on callbacks into the single JVM's program-DB lock
(70+ `decompile` processes, 197 JVM threads, ~1 RUNNING). To actually use 30–48 cores, use
**independent processes**: separate JVMs, separate databases, no shared project lock.
The trick that makes that cheap: **ZFS reflink-clone the analyzed `.rep`** — instant block sharing,
no repeat of the original 85-minute analysis. This is the original DragonShard insight and spirit.
The ceiling/throughput are observations from that operation, not a guarantee for every Ghidra build.

Bulk computation runs in independent **headless JVMs**, never through a queue of interactive
Ghidra MCP calls. MCP supplies SystemSage telemetry and optional interactive inspection only.
The original recipe starts with an already-analyzed project; the additional analysis stages below
prepare incomplete sources before applying that same independent-worker export mechanism.

DragonShard-2 preserves the army. It upgrades what the army brings home: full evidence that remains
searchable, instead of a disposable scan for one project's hardcoded targets. The original skill is
untouched; [provenance.json](provenance.json) records its hashes. The CLO-specific `CloShard.java`
is intentionally absent here at Cencyte's request. Its essential independent-JVM, `i % N`, timeout,
and shard-output mechanisms survive in the generic exporter.

## Recipe

### 0. MANA CHECK FIRST — mandatory, no fallback around a broken sage
Consult **SystemSage MCP** before every heavy cast. Verify initialization, tool listing, and actual
memory/CPU/disk calls; a running process or configured server is not proof. Read direct kernel
MemAvailable, swap, memory pressure, disk and cores alongside it; consult thermals when available.
**If SystemSage is broken, repair it before launching.** Direct telemetry supplements the sage.

Size N to the real reserve, not nominal RAM. Historical caster RSS was ~1.1–1.5 GiB with a 900 MiB
heap plus decompiler and database cache. The launcher budgets 1.5 GiB/worker + 8 GiB reserve.
Forty workers therefore initially require 68 GiB MemAvailable. Swap is an emergency buffer,
not extra worker capacity. Update measured resource budgets as actual RSS becomes available.

Cencyte wants the full workflow, not a mini substitute. Never quietly reduce the agreed corpus or substitute a cut-down implementation. Report the
planned worker count and measured constraints. Fewer concurrent workers can still process the
complete corpus; explain the throughput tradeoff when proposing that resource-driven choice. A small isolated fixture is a tooling test, not the promised corpus run.

The bundled launcher calls `scripts/sage_probe.py` using a manifest-configured MCP virtualenv.
On this workstation SystemSage 0.1 / MCP 1.29.0 / psutil 7.2.2 are preserved in
`~/Source2/AI/MCP/SystemSage/.venv`; `~/bin/mcp-wrapper-systemsage` launches it without downloading
anything. Startup banners go to stderr. Never filter JSON-RPC by key order or mix stderr into it.
The repaired launcher, entry point and observed environment versions are preserved in
`assets/systemsage/`; the frozen version list records the existing environment, not an instruction
to install or upgrade packages. Older unrelated package pins remain in force.
A stale client tool catalog can be worked around with a fresh **actual MCP stdio session**, as the
probe does. It cannot be worked around by claiming direct `free` output came from SystemSage.

### 1. Prepare stable analysis projects, then reflink-clone N times
For a raw filesystem clone, save and close the source Ghidra project first. An open database copied during writes is
not a consistent research snapshot. Never remove the source `.lock` or `.lock~` to force access.
The launcher refuses source locks and checks file sizes/mtimes before, during and after cloning;
keep the original closed through clone creation. This check is not a substitute for database
quiescence or an application-consistent snapshot.

Use a **new run directory**, preferably on the same reflink-capable filesystem/ZFS dataset.
Each worker gets its own `.gpr` and `.rep` via `cp --reflink=auto`: share copy-on-write blocks when
supported, otherwise make an independent full copy. No hardlinks, no clones on `/tmp`, and no
shared mutable original database. Full logical clone size is already budgeted; do not assume
reflinks on an NTFS destination. The launcher refuses existing run directories rather than wiping them.

Reflink is initially instant/shared. Read-opening can diverge clones substantially as Ghidra
rewrites indices/caches even in read-only mode. Budget the measured source *logical* size × N,
plus raw exports, SQLite and free-space reserve. Compression can improve actual usage; don't
spend that saving before measuring it. A focused source project containing the entire selected
corpus can reduce this budget without reducing analysis scope. Prepare/verify such a project
explicitly if needed; the provided runner does not delete programs from the source.

**Validated alternative for an open GUI, including XRDP:** save all source programs through MCP,
then run `DragonShardStage.java selection.json staging.json` as a pre-script in a fresh empty
headless project. It opens the original with `DefaultProjectData(locator, false, false)` and uses
Ghidra's `DomainFile.copyTo` API to copy committed program databases into the new project. It
preserves folders, validates binary SHA256, records function/symbol counts, and rejects a source
modified during its copy. This reads saved analysis, not unsaved GUI edits; avoid concurrent
editing/analysis during staging. No raw copy of the live `.rep`, source lock removal, project
closure, or MCP script-execution permission change is needed. After staging exits, the fresh
closed project is the ordinary source for the independent reflink army. This also compacts the
selected databases: the first seven GHUB programs occupied about 90 MiB logical in the real run.

### 2. A shard GhidraScript — deterministic partitioning and persistent evidence
**Audit the analysis before exporting.** An Analyzed flag or a large function count is insufficient.
The October 6 run exposed mostly one-byte placeholder function bodies with no listing instructions;
Ghidra's decompiler still generated substantial C, but the instruction-reference exporter had
almost no call graph to record. The initial 99.995% decompiler success rate did not establish
analysis completeness. Never equate those measurements again.

Use `DragonShardDeepen.java report.json SHA256 audit` on a research copy to measure function entry
instructions, executable instruction/body coverage, references, missing entries and unresolved
indirect calls. `analyze` mode seeds missing disassembly at existing entries, repairs placeholder
bodies, and runs `analyzeAll` plus pending analysis with the recorded program settings. It retains
names/annotations and never clears the original GUI database. Run writable analysis only on
isolated clones. Undefined executable bytes can include padding/data; they are not a direct
percentage of missing functions. Default analyzers likewise cannot prove exhaustive discovery.

Every export manifest program now requires `analysis_audit`, pointing to a completed same-hash
report. Preflight rejects no-instruction listings and cases where more than 1% of functions lack
an entry instruction. This is a minimum quality gate, not proof of semantic understanding.
For a completed initial campaign, `scripts/depth_campaign.py ORIGINAL_CAMPAIGN` prepares a
separate `depth-v2` corpus in the same 7/7/6/1 order. It uses four independent analysis workers
(4 GiB heaps; the large agent gets 12 GiB), then resource-sized export workers with 60-second
function timeouts. It reuses the verified CoreMessagingXP pilot for this campaign. Do not rerun
into an existing owned depth directory without inspection. `--resume` reuses verified complete
batch indexes and saved complete analysis reports only after the previous coordinator exits.
Unstarted/partial directories need explicit inspection and preservation before recovery; this
is not arbitrary interrupted-shard resume.

`--analysis-workers 8 --max-export-workers 48` raises the ceilings on this 48-thread machine;
actual concurrency remains memory/disk gated. The measured 31-worker DLL export peaked at
15.5 GiB total cgroup memory, allowing a cautious DLL-worker planning budget of 1.25 GiB;
the large agent keeps 1.5 GiB. `analyze_ahead.py CAMPAIGN BATCH SHA256` can start the large
agent in its own owned project/service before its export batch. The coordinator waits for and
adopts its complete saved analysis, while reserving the ahead service's remaining memory cap.
Track that service in `depth-v2/ahead-service.json`. Keep the independent service disk/memory
guards; do not oversubscribe based only on its initially small RSS. The October 6 live ARC cap
was reduced to 16 GiB for this boost; the persistent boot setting remains 32 GiB.

**Distribute the analysis itself when one large binary is the critical path.** Export concurrency
does not parallelize `analyzeAll`. The installed x86 constant-propagation analyzer defaults to two
threads because they contend on one Program DB lock. Raising that thread count does not remove
the lock. `scripts/parallel_analysis.py` and `DragonShardAnalyze.java` provide a separate route:

1. Prepare a saved disassembly/body-repair checkpoint using a private read-only view of a closed
   source; preserve the original. Export an immutable packed database and native-function inventory.
2. Partition native functions by estimated body size across 42 independent JVMs. Each JVM opens
   the packed database with its own mutable DBHandle, caches, change set, transaction and lock.
   The unpacked immutable backing may be shared using Ghidra's packed-database cache. This is not
   concurrent mutation of a shared Program object. Workers call the stock `X86Analyzer.analyzeLocation`.
   Warm both immutable input and comparison views **before** starting the army: Ghidra 12.1.2
   holds a global cache lock while unpacking, and another opener gives up after 30 seconds.
   `warm_cache` prepares these once, avoiding failure after a worker has computed its entire shard.
3. Compute each worker's full database differences inside that worker, then save its database.
   Independent saves use byte-range lock permits; the coordinator recalculates permits from free
   disk, pending outputs, materialization scratch, host RAM and cgroup memory. Dirty NTFS page
   cache counts against the service limit. Never put the whole army behind one unconditional
   save lock. The merge consumer starts immediately and takes saved inputs as they become ready.
   On NTFS, measure kernel wait channels as well as CPU: independent scratch-file growth can
   contend in `ntfs_look_for_free_space` / `run_unpack_ex`. The coordinator reserves scratch
   extents with `FALLOC_FL_KEEP_SIZE`, leaving bytes and logical EOF untouched and retaining
   output/merge/disk reserves. `preallocate_scratch.py DIRECTORY` supplies the same bounded
   helper for an already-running coordinator; it exits when that analysis completes or fails.
4. Perform a three-way merge against the common checkpoint. Accept disjoint changes or identical
   overlaps; reject conflicting bytes, instructions, concrete types, references and comments.
   Stock constant propagation also generates conflicting `undefined1`/`undefined8` access-width
   hints. Where baseline and both variants contain only undefined data, and every other compared
   property agrees, preserve the established layout and record BOTH width observations in the
   merge report. This reconciles uncertain annotations; it does not infer a concrete type or
   assert byte-for-byte identity with every possible serial ordering. Verify the remaining merged
   regions. Retain failure evidence and the original paused analysis until the replacement passes
   its gates. `--resume-workers` reuses complete, identity/coverage-checked worker outputs;
   `--resume-merge` additionally adopts a completed merge covering those exact workers.
5. With `--finish-auto-analysis`, run the remaining analyzers and the orphan-instruction tail,
   then repeat parallel constant propagation until it introduces no changes. Reconcile at most
   four rounds; a nonconverged result is incomplete. Publish a closed source project and depth audit
   only after this gate. The global export queue can adopt `complete-analysis.json` using the
   pointer in `depth-v2/parallel-analysis.json`, then retire its superseded serial analysis.
   After a reboot, resume with `--checkpoint` pointing to the latest complete `finish-NN`, not
   the original seed. A zero-change reconciliation of that checkpoint directly satisfies the
   convergence gate. Unsaved worker reports do not constitute reusable database results.
   Update both service pointers, restart the disk guard, and let the export queue follow the
   owned parallel producer even when the original serial fallback service no longer exists.

```bash
python3 scripts/parallel_analysis.py /campaign/batch4/run/manifest.json /campaign/NEW_ANALYSIS \
  --workers 42 --finish-auto-analysis
```

`--checkpoint-only` prepares a reusable seed; `--checkpoint PATH --checkpoint-service UNIT`
waits for its owned producer. Explicit `--pause-unit dragonshard2-...` arguments freeze the old
analysis/idle queue after the checkpoint is ready, preserving their processes as a fallback.
They are thawed on exit. The production systemd service additionally uses `ExecStopPost=... thaw`
so a coordinator crash or cgroup kill also releases the fallback. Do not freeze user applications.

The fixture validation uses `--workers 2 --validate-serial --validation-strip-references
--finish-auto-analysis`. Reference stripping happens only in the fixture's private checkpoint;
it forces nonzero analysis changes and verifies the merged program against the serial pass with
Ghidra's complete ProgramDiff filter. Never use that validation flag on the production corpus.
The tested fixture converged after the remaining-analyzer pass. Full-binary convergence is still
a runtime gate, not a conclusion inferred from the small test.

The live October 7 army reported 42 Java analysis processes and 97.9% host CPU at startup.
Heap is 640 MiB per worker, with 1.05 GiB per-worker RAM admission plus 8 GiB host headroom.
Existing corpus outputs still commit 12 GiB of disk; it is pending data, not an unused cushion.
Worker packed-file caps equal the checkpoint size plus 32 MiB; preserve incomplete results if
that bound or the remaining-space commitment fails. Worker database replicas and unpack caches
are reclaimed only after their verified merge, retaining reports and the merged result.
Reports are under `round-NN/workers/*/report.json`; the Scrying Glass includes these live counts.

Watch progress without interacting with any worker PTY:

```bash
python3 scripts/watch_campaign.py /campaign/depth-v2
```

The terminal watcher shows indexed counts, shard progress, analysis completion, owned processes,
CPU, RAM and disk. GPU utilization is explicitly whole-system telemetry: this Ghidra engine has
no GPU compute backend. `q`/Ctrl-C closes only the watcher. `--once` prints a headless snapshot.
When asked to open it remotely, use the user's actual XRDP display and D-Bus session, not inherited
local-display variables. A `--separate` Konsole may open a new window for `--new-tab`; do not enable
security-sensitive D-Bus command injection or write to an existing/cached PTY to work around it.

`scripts/DragonShardExport.java` uses one `DecompInterface` per JVM and exports every enumerated
internal function where `ordinal % shards == shard`. Arguments:

```text
DragonShardExport.java /absolute/output.jsonl shard_index shard_count timeout_seconds expected_sha256 snapshot_id
```

The header records binary hash, project path, image base, architecture, Ghidra version and settings.
The runner supplies a snapshot ID derived from the saved source file size/mtime inventory; this
detects mixed run snapshots, not cryptographic equivalence of all database contents.
Function records retain C, signature, RVA/address, timing and explicit success/failure/timeout.
Symbols include imports/exports; instruction references include direct calls, referenced symbols,
defined data values and short raw bytes for GUID/vtable investigation. Indirect calls without
resolved targets are marked unresolved. This is an evidence index, not a claim that virtual calls,
all vtable layouts, or every interface identity have been solved automatically.

If ordinary decompilation fails, preserve lower-level evidence instead of erasing the failure.
`DragonShardExceptionEvidence.java request.json evidence.json` accepts a binary `sha256` and
`addresses` array. On an isolated read-only clone it exports instruction bytes, assembly and raw
p-code, retries ordinary decompilation with a 120-second limit, and separately tries `normalize`.
Normalized output is not an ordinary C-decompilation success; retain the original failure counts.
The first 20 deeply analyzed GHUB programs had five ordinary failures with full assembly/p-code
captured; two yielded normalized output. All 29 no-instruction records were Ghidra synthetic
thunks, not undisassembled native function bodies. Record exceptions by binary hash/address.

Shard zero exports the symbol table. Completion totals must agree with actual records. A crash or
cancellation leaves `.partial`, never a success marker. A completed export can still contain
failed decompilations: **export coverage and successful C coverage are different quantities**.

### 3. Launch independent headless `-readOnly` casters on the clones
Edit a manifest for the actual project, full selected binary hashes/paths, run directory and
resource budget. The G HUB manifest is [manifests/ghub-tray.json](manifests/ghub-tray.json).
From this skill directory:

```bash
python3 scripts/dragonshard.py plan manifests/ghub-tray.json
python3 scripts/dragonshard.py preflight manifests/ghub-tray.json
python3 scripts/dragonshard.py run manifests/ghub-tray.json
```

The runner assigns roughly two jobs/worker across the selected programs in priority order; each
job partitions its entire program. It reuses a worker's private clone only after that JVM exits.
This avoids booting 40 JVMs for every tiny DLL while retaining the full 40-process capacity.
The individual invocation is:

```text
GHIDRA_HEADLESS_MAXMEM=900m analyzeHeadless clone_dir ProjectName[/folder]
  -process program.dll -readOnly -noanalysis -max-cpu 1
  -scriptPath skill/scripts -postScript DragonShardExport.java output.jsonl i N 12 SHA256 SNAPSHOT_ID
```

**Use `GHIDRA_HEADLESS_MAXMEM`**, verified in local Ghidra 12.1.2. Setting only `JAVA_TOOL_OPTIONS`
can lose to the launcher's explicit heap setting. Use `-noanalysis` only with an inventory of the
existing analysis state. Some imported programs can contain many functions while their overall
Analyzed flag is false. Export those known functions, label that limitation, then investigate
function discovery/needed analyzers separately; don't silently claim full binary coverage.

`-readOnly` on a separate clone locks its own project. The shared original still fails when held
by the GUI. No manipulation of the live desktop is needed for this workflow.

### 4. Watch to N/N, then merge and index
Progress/logs stay in the owned run directory. Ctrl-C/SIGTERM addresses tracked process groups;
never `pkill -f` the army. The indexer checks completion, every expected binary/shard, function
ordinals, unique addresses, consistent program identity, and outcome totals before replacing an
index. It writes a SQLite FTS5 database plus direct-call/reference tables and a coverage report.

If a run stops, retain its raw completed and `.partial` exports and logs. Automatic resume/retry is
not currently implemented: inspect the failure, rerun only missing/failed work with the same
snapshot and partition settings in isolated clones, and reindex only a consistent complete set.
Do not overwrite finished evidence or combine a changed analysis snapshot under the same hash.
A changed partition/analysis requires a separate corpus version. Rebuild command:

```bash
python3 scripts/corpus.py index /run/exports /run/corpus.sqlite --manifest /run/manifest.json
python3 scripts/corpus.py search /run/corpus.sqlite 'RegisteredWait OR ActivationConfiguration' --limit 10
python3 scripts/corpus.py function /run/corpus.sqlite FULL_SHA256 GHIDRA_ADDRESS --max-chars 12000
python3 scripts/corpus.py failures /run/corpus.sqlite --limit 100
```

The search results cite binary identity and addresses. Function retrieval returns bounded C and
callers/outgoing references; unresolved indirect edges remain unresolved. Read the few relevant
functions and their neighbors, not megabytes of exports. Preserve raw results as the durable
source of truth. No embedding downloads, cloud indexing or LLM call per function is necessary.

### Full 21-program campaigns
**Prefer the global dependency queue for new runs.** Cencyte's “seek forward” requirement means
that a binary waiting on its own database/analysis never blocks ready work belonging to another
binary. `scripts/swarm.py` schedules independent staging, analysis, export shards, gap audits and
indexing across the full manifest. An index waits only for its own binary's shards. The queue
backfills smaller ready jobs when a larger one cannot fit RAM/disk; completed workers release
capacity immediately. It creates up to four shards per CPU slot (target 1,500 functions/shard)
to reduce long export tails without duplicating analysis. Every recognized function is visited.

```bash
python3 scripts/swarm.py /campaign/campaign.json /campaign/NEW_SWARM_DIRECTORY \
  --adopt-depth /campaign/depth-v2 --cpu-slots 48 --disk-reserve-gib 0
```

Omit `--adopt-depth` for a new corpus. Adoption verifies completed batch indexes and retains their
paths, then adds detailed gap audits. It recognizes an owned independent `ahead` analysis service
and waits for its saved, closed completion marker without stopping, restarting or raw-copying
its live database. Run only one coordinator for the scope; stop an older controller only after
verifying that it has no active child work. Keep the independent analysis service running.
The current live queue is recorded in `depth-v2/swarm.json` and the launch reference.
`--adopt-queue PRIOR_IDLE_SWARM` can additionally reuse verified gap reports with identical
source-snapshot identities; it is not a general unfinished-worker resume facility.

`DragonShardGaps.java` records every undefined executable range, instructions outside function
bodies, unresolved computed call/jump, missing entry instruction, and Error/Warning bookmark.
It scans complete undefined ranges for padding-like bytes but does not declare such bytes proven
non-code. Synthetic external thunks are counted separately from missing native instructions.
The gap audit feeds `DragonShardIndirect.java`: group unresolved sites by owning function, then
partition those functions into independent jobs of about 128 functions. Export the complete
backward SSA dependency graph of each target, with shared IDs for cycles/phi nodes. Preserve
unmatched high-p-code sites, timeout/failure results and sites without function owners. These
slices expose target computation; they do not prove the eventual runtime target. This supplies
useful cross-binary work while another binary is in a serialized database pass.
The queue requires the index, gap audit and indirect-evidence jobs before reporting completion.
**That is completion of the export/audit scope, not complete semantic recovery.** Follow the
addressed gaps that can affect the required behavior; preserve unresolved results explicitly.
Enabling every speculative analyzer or repeatedly exporting unchanged C is not a completeness
proof. Record analyzer settings, review unknown ranges and indirect dispatch, preserve assembly/
p-code for decompiler failures, and use differential execution for runtime-dependent behavior.

Resource gates use MemAvailable, active worker growth, independent analysis growth, and the
service's cgroup memory limits. SystemSage is probed before dispatch and periodically while ready
work remains. The CPU limit is a capacity ceiling, not a promise that serialized program-database
passes or a final single task can use every core. Never burn cycles merely to draw 99% on a meter.
Keep all useful ready work running subject to measured RAM and disk commitments. For Cencyte's
48-thread workstation, target **40 or more useful workers whenever permitted** (42 analysis
workers, a 48-CPU global queue), and refill freed slots without waiting for a whole cohort.
Show computing, database-diff computation, saving, merge verification and explicit capacity gates
separately. A completed computation pass is not a completed database or export.

**October 7 storage update:** the current corpus is at
`/mnt/Captures/DragonShard-2/Penguin-GHUB`, with the original workspace path retained as a symlink.
Captures is NTFS on a separate SSD. `relocate_campaign.py` copies and fsyncs closed files, verifies
SHA256 by reading the destination, and only then replaces the source with a link. Live writable
files remain attached to their original inodes until a saved checkpoint; `complete_storage_move.py`
finishes that handoff and starts the destination queue. Never copy, unlink and resume an open
writable database against two diverging sets of files. NTFS timestamp rounding is recorded in
`.dragonshard-source-times.json`; `source_state` preserves the previous snapshot ID only while
size and copied timestamp match that verified migration record. Changed files invalidate it.
The new user instruction to prevent disk exhaustion supersedes the earlier zero-cushion choice:
the current queue/analysis protect **16 GiB on Captures**, and `disk_guard.py` freezes owned jobs
if the system drive drops below **20 GiB**, Captures drops below 16 GiB, or that mount disappears.
It resumes only pauses it owns. The retained old fallback stays frozen (`--leave-paused-unit`)
until a validated replacement allows it to close and migrate. All of this remains independent of
chat prompts; a reboot still requires inspection of saved state.

Historically, the global queue defaulted to **zero unused disk cushion**, as requested then.
Space for unfinished evidence and potential clone divergence is still reserved. It budgets the
full source logical size per active clone, enforces byte caps on new export/gap files, and caps
SQLite pages while using no disk rollback journal or disk temp files for the disposable new index.
Record a task-budget failure and preserve raw/partial evidence; never silently truncate or mark
it complete. Local free-space checks cannot guarantee against unrelated processes consuming the
same filesystem concurrently; these are application bounds, not an exclusive filesystem quota.
Keep checking actual free space and stop dispatch if commitments no longer fit. The existing
independent analysis retains its own resource guard; do not interrupt it merely to change policy.

The Scrying Glass reads `depth-v2/swarm.json` and shows active/ready tasks, per-binary shard
completion, gap-audit coverage and explicit CPU/memory/disk/dependency gates. `queue.json`,
`status.json`, `events.jsonl` and the combined `catalog.json` persist the real state. New run
directories are exclusive. Automatic in-place resume of a partially executed swarm is not yet
implemented; inspect its evidence before recovery into another directory. Do not rerun completed
heavy stages to make a utilization graph look busy.
Run the coordinator as a user systemd service independent of the terminal. It automatically
follows dependencies through completion; no conversation prompts are needed. A failed task leaves
independent jobs running, then reports incomplete status after all remaining runnable work is
exhausted. Global resource/access failures stop owned tasks with evidence retained. A completed
scripted campaign still does not claim every indirect target resolved or full Wine compatibility.

The earlier batch runner remains available for reproducing recorded runs:
The 2026-10-06 launch selects all 21 programs in four priority groups (7 / 7 / 6 / 1).
Its complete selection is [manifests/ghub-all21.json](manifests/ghub-all21.json); this is the
campaign inventory, not a command to rerun into the recorded output directory.
Use separate compact staging projects and full manifests per group; this preserves the entire
scope while avoiding cloning the huge agent database for small DLL jobs. The tested campaign is
recorded in `references/launch-20261006.json`. `scripts/campaign.py CAMPAIGN_DIR --first-unit UNIT`
continues after the first background service, verifies each index, consults SystemSage, sizes the
next army by RAM/disk, stages the next group, and runs it. It preserves failed/partial evidence
and reports `blocked_or_failed` if a prerequisite fails. It never silently omits a group.

The campaign may reclaim only verified run-owned clones after all workers using them exit; raw
exports, closed staging sources, logs, manifests and indexes are retained. The legacy batch runner
keeps an 8 GiB disk reserve; the global queue above implements the current zero-cushion preference.
Use a user systemd service with
memory limits so XRDP disconnects do not kill the run. This does not survive a machine reboot;
inspect persisted status and completed evidence before any restart, rather than rerunning into
an existing output directory.

`catalog.json` lists the completed batch databases without duplicating their contents in another
large index. Query every indexed batch with:

```bash
python3 scripts/corpus.py search-catalog /campaign/catalog.json 'NtCancelWaitCompletionPacket' --limit-per-batch 5
```

Results explicitly report whether the entire campaign is complete. Ranking is within each batch.
The initial export campaign completed 223 shards and indexed all 21 programs: 560,609 recognized
internal functions, 560,582 successful decompilations, 24 timeouts and 3 decompiler errors.
All exceptions remain addressable in the indexes; this is not a claim of complete function
discovery, semantic understanding, or Wine compatibility. Its listing/reference coverage failed
the subsequent depth audit; use the separate `depth-v2` corpus for new contract research as its
batches become available. The original remains useful baseline evidence.
Ghidra's total function count can include external/import functions excluded by its internal
function iterator; report that difference separately from failed decompilations. Real first-batch
example: 59,088 reported functions, 59,055 internal records, 59,054 successful C results, 1 timeout.

### 5. Clean up
Only after coverage and the index are verified, identify the exact run-owned `clones` directory
using `owner.json` and confirm no worker still uses it. Delete that directory if authorized by the
run's cleanup scope, retaining exports, logs, manifest, source-state record, coverage and index.
Never delete the analyzed source or use a broad wildcard. The original runner does not auto-delete
clones. The global queue deletes only its own per-task clone after the worker exits and its output
validates, so completed tasks promptly release storage for the remaining army.

## Gotchas (learned the hard way)
- `/tmp` is tmpfs here: tiny logs are fine, **never the reflink clones or full corpus**.
- `pkill -f` / `pgrep -f` can self-match. Use tracked child process groups, or identify Java by
  comm plus exact `/proc/PID/cmdline`; never kill by a literal in your own command line.
- Giant functions can exceed 12 seconds. The original CLO run got 303,887 / 312,176 functions
  (97.3%) in ~17 minutes with 40 casters versus ~3 hours in one instance. Those are historical
  measurements, **not a GHUB throughput forecast or permission to ignore timed-out functions**.
  Review failures intersecting the active contract and retry deliberately with larger limits.
- Full exports make research reusable; they do not make decompiler output executable source,
  eliminate ABI/lifetime/threading work, or prove a tray, device or driver feature works.

## Upgraded investigation and reporting
Read [references/ghub-roadmap.md](references/ghub-roadmap.md) for this operation's exact priority
order, current evidence, resource gate and exit criteria. Read the machine inventory and manifest
before relying on remembered filenames; two agent versions have very different purposes.

1. Export/index the agreed full corpus once. Label recognized-function coverage separately from
   successful decompilation, research understanding, implementation and end-to-end validation.
2. Search the current blocked contract, recover callers/callees, identify thread/lifetime/error
   behavior and consumers. Use live Ghidra for unresolved indirect edges and types, not repeated
   fetching of already exported C. Identify bundled third-party library code before spending
   reasoning on hundreds of thousands of agent functions.
3. After the analysis stage, perform the approved Windows/Wine differential probes: identical
   minimal callers, object identity/refcounts, registration/cancellation, callback thread/order,
   return values and teardown. Trace the precise registered-wait/composition sequence. Fix the
   earliest divergent contract; add a regression test that distinguishes the real behavior.
4. For a Wine-side GL failure, inspect Wine's own source and reproduce the context/thread defect.
   Do not decompile extra Windows libraries when the relevant broken implementation is local.
5. Keep native dependencies when Wine can host them; reimplement the required OS boundary rather
   than rewriting vendor XAML/agent DLLs wholesale. Do not treat another permissive `S_OK` stub as
   a completed subsystem. Native Windows kernel-only contracts may need a Linux-side service.
6. Report completion percentages, remaining scope and measured coverage proactively. Distinguish
   export, understanding, implementation and acceptance denominators. Give confidence, assumptions
   and ranges; label speculation. Cencyte prefers these over duration forecasts: do not supply
   engineering hours or other time estimates unless asked. Revise after exports and differential
   traces, and surface a better strategy when returns drop.

The more efficient overall strategy is **DragonShard's durable static corpus plus differential
contract testing**, not blind full decompilation followed by one crash fix at a time. Native trace
setup follows analysis here, as Cencyte requested. The original memory/operation remains at
`~/.codex/memory/imported/claude-home/{dragonshard_headless_army,clo3d_headless_analysis}.md`.
