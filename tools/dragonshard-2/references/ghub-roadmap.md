# G HUB / DragonShard-2 plan of attack — 2026-10-03

This is the requested scope/forecast, grounded in the live `ghub2remapper` inventory and
`tools/wine/installation_probe_results.json`. Estimates are planning judgments, not measured
percentages or promises. The original DragonShard remains unchanged.

## What exists, what is finished, and what “done” means

Ghidra contains **23 programs / 889,189 recognized functions**. The folders in the OCR listing
are organizational folders, not additional DLLs. The inventory also found the tray executable,
InputHost, user32, two win32k references, DWrite, wuceffectsi, and a second agent version.
[ghub-inventory.json](ghub-inventory.json) preserves exact project paths, hashes, source paths,
counts and analysis flags. All 23 source files existed when checked.

**Zero of these 23 entire binaries has been exhaustively understood or replaced. That is not the
completion target.** Most native user-space binaries can remain native under Wine. Windows
reference DLLs and kernel files tell us the OS contracts Wine must supply. The job is to reproduce
the needed semantics at those boundaries, not rewrite 889,189 functions. Twenty programs are
worth indexing in the full first corpus; seven are immediate research priorities. One installed
agent belongs to the later device/profile workstream. Two programs can be parked.

Existing positive evidence:

| Acceptance area | Current evidence | Remaining limit |
|---|---|---|
| Installation | The cured installation/launch route works | Not a certification of future updater versions or clean untouched installer behavior |
| Agent startup / IPC | Agent remains running; `/feature_flags`, `/updates/channel`, `/devices/list` answer | Full service lifecycle/device functionality not established |
| Main Devices UI | User saw the UI; recorded Devices screen and onboarding work | The separate tray is a different executable/path |
| Mouse/keyboard discovery | G502 X PLUS and G915 X LIGHTSPEED TKL report ACTIVE | ACTIVE does not demonstrate G-Shift or global remapping |
| Full tray | InputSite/configuration factories and default setters now pass | All 11 probed windows unresponsive; rendering/input/teardown not accepted |
| G-Shift / profiles | Prototype and agent research exist | Global input, profile switching and persistence not yet validated |
| Yeti Nano | Enumerated | Still BLOCKED; audio integration unresolved |
| Reliable daily use | Working and experimental environments isolated | Cold launch, reconnect/suspend/restart and final deployment regression remain |

Four of eight areas have positive milestone evidence; **4/8 is not 50% of engineering work**.
My low-confidence planning estimate is roughly **40–60% of the stated three-device user workflow
has a usable foundation**. The hardest cross-process input/composition or device work could still
consume most remaining effort. No new Wine behavior was implemented in the DragonShard tooling
turn, and no production G HUB corpus has yet been exported with DragonShard-2.

The latest narrow fix (`d8d0e9a`, activation configuration) passed 12,414 adapter/configuration
checks, 4 disabled checks, 4,191 InputSite checks, 62 UISettings checks and CoreWindow regression.
It cleared a fail-fast; it did not clear tray responsiveness. It stores configuration policy but
does not supply the complete Bamo/compositor input transport or non-default policy effects.

A component is “done for this target build” only when its required ABI, return/error behavior,
object identity/lifetime and thread/event semantics are understood sufficiently, implemented or
hosted, covered by distinguishing tests, and its consumer passes the relevant integrated scenario.
Record binary/version and scenario. A whole DLL, elapsed analysis time, successful factory call or
absence of the previous error message is not an acceptance criterion.

## Full first corpus and priority order

All 20 programs below are included in one full manifest: **209,941 recognized functions**, queued
as **89 shard jobs over 40 independent workers**. This is the complete selected tray/reference
scope, not a reduced DragonShard substitute. Priorities determine the investigation order.

| Priority | Program | Functions | Work / exit criterion for this target |
|---:|---|---:|---|
| 1 | CoreMessagingXP.dll | 3,707 | Map registered-wait wake creation, drain, cancellation and ownership; explain repeated runtime samples and match native callback ordering/teardown |
| 2 | CoreMessaging.dll | 5,350 | Resolve shared dispatcher/queue contracts used by the XP/native consumers; no stuck waits or use-after-close in the matching scenario |
| 3 | Microsoft.Internal.FrameworkUdk.dll | 4,176 | Identify adapter ABI and ownership across input/dispatcher calls; consumers match both normal and failure paths |
| 4 | InputSinkReference/InputHost.dll | 10,440 | InputSite/configuration/sink routing, identity, closure and required policy effects; native/Wine contracts and real input scenario agree |
| 5 | Microsoft.InputStateManager.dll | 1,701 | Determine registration and activation state transitions; consumer proceeds with correct state, not just bypassed fail-fast |
| 6 | Microsoft.UI.Input.dll | 6,252 | Connect public/lifted input routing and event delivery; required mouse/keyboard/focus events reach the tray correctly |
| 7 | DispatcherQueue/lghub_system_tray.exe | 27,462 | Consumer call sites and lifecycle; usable tray/menu, show/hide main UI, correct input, clean quit/relaunch |
| 8 | dcomp.dll | 9,910 | Public composition object/surface/commit contract; lifecycle and visible updates match |
| 9 | dcompi.dll | 8,182 | Private composition relationships needed by this tray; distinguish required paths from unreachable features |
| 10 | dwmcorei.dll | 7,465 | Reference compositor ownership/synchronization; map only required transport behavior, not rewrite DWM |
| 11 | d3d11.dll | 7,413 | Shared-resource/fence/texture lifetime; actual Wine GL context defect is investigated in Wine source |
| 12 | d2d1.dll | 12,234 | Composition surface producer, brush/draw/commit relationships; visible frame tests for used paths |
| 13 | DispatcherQueue/wuceffectsi.dll | 1,775 | Identify effects/queue dependencies exercised by the tray; verify relevant creation/lifetime/rendering paths |
| 14 | TextAnalysisReference/DWrite.dll | 6,280 | Reference the private text ABI already patched; correct fonts/layout and no text regression |
| 15 | Microsoft.ui.xaml.dll | 61,059 | Index full library, investigate only reached consumer chains; identify needed host contracts and validate actual XAML interaction |
| 16 | twinapi.appcore.dll | 12,138 | Activation/core-app lifecycle, relevant broker calls and error semantics; cold launch/quit/relaunch work |
| 17 | marshal.dll | 2,248 | Used marshaling interfaces, thread handoff and lifetime; round trips/errors and cleanup agree |
| 18 | InputSinkReference/user32.dll | 2,826 | User/kernel boundary for required input sinks/window routing; map observed calls and integrated input behavior |
| 19 | InputSinkReference/win32kbase.sys | 9,252 | Reference OS-side sink implementation and invariants; implement needed equivalent in Wine/Linux boundary |
| 20 | InputSinkReference/win32kfull.sys | 10,071 | Reference only reached input/window mechanisms; validate the replacement behavior, do not try to load this driver into Wine |

Groups 1–7 = **59,088** functions; 8–14 = **53,259**; 15–20 = **97,594**.
Indexing a reference kernel file does not imply that its driver can or should run in Wine.
The two graphics failures and dispatch/input paths may turn out independent: track them separately
until runtime evidence establishes causality. One captured stack does not establish a deadlock.

Later/parked:

| Program | Functions | Decision |
|---|---:|---|
| SavedWineAnalysis/lghub_agent_saved_wine.exe | 351,250 | Installed 2026.4 agent: full later corpus for G-Shift, profile/device and Yeti work. Fingerprint bundled libraries before deep reading. Adds up to 561,191 functions with the first corpus. |
| lghub_agent.exe | 315,644 | Older version: park; compare only when a version-specific question justifies it. Do not spend full export/reading effort twice by default. |
| lghub_installhelper.dll | 12,354 | Park while the cured installation path works. Reopen for install/update regressions; not declared universally finished. |

Full successful export still does not establish complete code discovery: 13/23 programs currently
report Analyzed=false despite populated functions. Review recognized-function counts, executable
coverage, symbols and discovery failures. Rerun necessary analyzers on research copies where
coverage evidence demands it, rather than blindly reanalyzing all programs.

## Concrete workflow and stronger strategy

1. **Prepare the full cast.** SystemSage must initialize and return metrics. Save/close the source
   project for consistent copies. Make resource headroom described below. Run the ready manifest.
2. **Preserve and index the evidence.** Raw per-shard JSONL contains C, symbols, instruction
   references, unresolved indirect calls, identity/provenance and failure records. SQLite indexes
   function text and reference terms with direct-call/reference edges. Indexing rejects missing
   programs/shards, duplicate addresses or mixed snapshot metadata. Report all export and
   decompilation denominators; retry high-value failures explicitly.
3. **Investigate the first proven boundary.** Query RegisteredWait, ProcessWakeCompletion,
   DrainOffThreadCompletions, WaitController and the active consumer. Recover callback ownership,
   cancellation, reentrancy, notification-reset behavior and queue-thread assumptions. Use Ghidra
   to resolve only uncertain types/indirect edges. Save address/hash-linked conclusions once.
4. **After analysis, establish the approved native oracle.** Use the existing Windows installation
   or VM and a small C++ contract probe for the same objects/calls/events in Windows and Wine.
   Record return values, COM identity/refcounts, thread IDs, callback order and cancellation/close.
   For the actual tray, use focused trace/debugger recording around the wake/composition sequence;
   repeat Wine stack sampling to distinguish a completion storm, blocked wait or unrelated sample.
   Microsoft TTD is useful for supported user-process execution; it is not a capture of every
   GPU/kernel/other-process state. See [Microsoft's recording documentation](https://learn.microsoft.com/en-us/windows-hardware/drivers/debuggercmds/time-travel-debugging-record).
5. **Fix the earliest semantic divergence.** Implement/test the contract across the required
   interface family, then run the integrated tray. Separately reproduce the GL-context failure
   and inspect Wine's own context/texture code. Don't turn unrelated log warnings into blockers
   without showing that they prevent an acceptance scenario.
6. **Accept the tray as a subsystem.** Healthy windows, visible presentation, focus/input, menu
   actions/show-hide, quit/relaunch, repeated cold starts and relevant failure-path cleanup.
   A live process or another passing factory is insufficient. Keep user-driven visual checks
   persistent and without short deadlines; use private/headless automation first.
7. **Device workflow next.** Export the installed agent, identify bundled standard libraries,
   trace G-Shift/profile service operations and device reports, resolve Yeti Nano, then validate
   the actual user's devices. Decide from evidence whether a Linux-side service is needed for
   a kernel-only boundary; don't spend weeks accumulating stubs that cannot deliver its behavior.

The stronger method is **static corpus + native differential contract tests + Wine source
instrumentation**. DragonShard is the static-analysis accelerator, not the entire research method.
This breaks the costly cycle of one missing API per prompt and avoids re-reading known C. A
separate Linux companion is an architectural contingency if a required Windows kernel contract
cannot be hosted; it does not replace the user's chosen Wine objective without discussion.

## Historical preflight — October 3, superseded by the launch update below

SystemSage was repaired and verified via real MCP: **52 tools**, successful memory/CPU/disk calls.
The wrapper uses a preserved local compatible venv; no package updates were installed. Codex's
entry is enabled. The already-running Codex tool catalog may remain stale until a restart;
DragonShard's fresh MCP session works now and is required by the launcher.

At 23:03 local, **14.69 GiB MemAvailable**, **89.37 GiB swap occupied**, **42.17 GiB disk free**,
48 logical CPUs. The source database is approximately **1.6 GiB physically allocated / 3.68 GiB
logical**. These are live measurements, not fixed machine limits.

Forty workers is our proposed full-power configuration, not a number explicitly required by the
user. A lower worker count could preserve the entire corpus and all DragonShard-2 functionality,
with a longer runtime; that should be an explicit scheduling choice, not a mini implementation.
The prepared 40-worker manifest asks for **68 GiB MemAvailable** and a conservative **177 GiB
free disk** (40 × logical project size + 20 GiB export/index budget + 10 GiB reserve). This is a
worst-case planning gate, not a measured prediction that all clones will consume 147 GiB. It can
be revised from representative clone-divergence/output measurements, or by preparing a smaller
source project containing all 20 selected programs. Such preparation does not reduce corpus scope.

**At that preflight, no full GHUB army had launched.** The preflight rejected memory, disk and the live
project lock. The tiny two-worker fixture tests the tooling only. Do not silently change the
40-worker plan, close applications, stop VMs or delete files to bypass these conditions.
Two running QEMU VMs accounted for roughly 40 GiB resident memory earlier in this task; closing
both would help substantially but does not by itself guarantee the 68 GiB target. ARC was about
18 GiB. The live and persistent ARC maximum are already **32 GiB** in
`/sys/module/zfs/parameters/zfs_arc_max` and `/etc/modprobe.d/zfs.conf`. There is no missing cap
to fix. A reboot is not required to change the live cap, and lowering the cap does not guarantee
immediate reclamation; see [OpenZFS parameter documentation](https://openzfs.github.io/openzfs-docs/man/master/4/zfs.4.html).
No VM, source project or backup data was stopped/deleted in this preparation.

## Current measurement policy (updated 2026-10-06)

Cencyte requests percentage/coverage estimates instead of duration forecasts. The earlier
engineering-hour estimates are superseded. Report export coverage, contract understanding,
implemented behavior and integrated validation separately. The functional foundation estimate
remains **40–60% for the stated current-device workflow**, low confidence; launching an export
does not advance that implementation percentage by itself.

The full campaign now includes the installed agent: **21 programs / 561,191 recognized functions**,
in four complete priority batches of **7 / 7 / 6 / 1**. Read
[launch-20261006.json](launch-20261006.json) and the campaign's persisted `status.json` / `catalog.json`
for measured progress. Program count and function count are different denominators, and neither
is a measure of required behavior understood. Count unresolved calls and newly discovered
required interfaces as research uncertainty, rather than guessing that all exported C is understood.

The full run completed: **21/21 programs, 223/223 shards, 560,609 recognized internal functions**
indexed. Decompilation produced C for **560,582 (99.995%)**, with 24 timeouts and 3 explicit errors.
The difference from 561,191 is 582 external/import functions, not lost internal export records.
All four indexes passed integrity checks; temporary worker clones were reclaimed after validation.
Function discovery is limited to the saved Ghidra analysis state. Wine implementation and
end-to-end acceptance coverage did not advance merely because the static corpus is now available.

**Depth audit correction:** the initial source databases mostly contained one-byte placeholder
function bodies and almost no persisted disassembly. The installed agent's initial index has
zero instruction references despite substantial decompiled C. Therefore, the initial export is
complete only relative to its original function list; it is not adequate call-graph evidence.
The isolated CoreMessagingXP deeper-analysis pilot expanded 3,707 to 4,082 functions, from zero
to 170,271 listing instructions, and from zero to 71,075 instruction references (17,261 direct
call references). A separate `depth-v2` campaign now analyzes and reexports all 21 programs.
Read its own `status.json` and coverage reports for the corrected research corpus. Preserving
the first export records the baseline without mistaking its success rate for analysis depth.

Both VMs were shut down gracefully for this launch. SystemSage native MCP and fresh-stdio probes
passed. Instead of cloning the large original GUI project, saved program databases are copied
through Ghidra's read-only project API into compact, independent batch projects. The source GUI
and MCP remain available on display :0; XRDP needs no window transplant for the headless army.
The first seven-file staging project is about 90 MiB, and its 32-worker preflight budget is
16.8 GiB disk / 56 GiB available RAM. This supersedes the old 177 GiB whole-project clone estimate
for this batching method, while preserving every program in the selected scope.
