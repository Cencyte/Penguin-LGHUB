# Chapter 13: a Desktop handoff and the native G-Shift path

The requested handoff is a Desktop launcher for the tested composition stack,
followed by G-Shift/profile work through Logitech's own agent. Chapter 12's eight
regression runs and 241 display samples remain implementation evidence; actual
Plasma menu appearance and actions still need user acceptance.

## Installation and launch evidence

`prepare_tested_desktop.py` copies a stopped prefix and the selected build
components from a successful native resize replay. The new installation is
`wine-lab/codex-run/desktop-tested-20261012`. Its manifest verifies SHA-256 for
36 files: application binaries/archive, experimental DLLs, matched Wine
server/NTDLL/Win32u components, and launcher/probe scripts. Other Wine modules
come from the pinned host Wine 10.20; this is not a standalone Wine distribution.
If an application update changes a checked file, subsequent launches refuse
that changed combination. No host DLLs or packages were installed.

The executable entry is `/home/firesongz/Desktop/G HUB (Tested Wine).desktop`.
Its context actions quit this session, save a read-only device/profile snapshot,
and open logs. Testing has no user deadline. Scoped shutdown checks the session
PID's start time, boot ID and command, then signals through a pidfd. Namespace
teardown stops only this dedicated prefix.

The first coordinator started an agent, UI and its own delayed tray. The native
UI also starts a tray. Two combined attempts reported an agent access violation
at builtin `ucrtbase+0x58153`, in floating-point parsing while accessing locale
information. Repeated agent launches were also observed. A probe timeout initially
obscured whether a replacement agent had become ready.

An agent-only debugger run reached ACTIVE mouse and keyboard states and answered
five API queries. Opening the UI after agent startup also left a responsive
agent, one main UI and one tray. That supported correcting startup ownership
before patching the C runtime. It does **not** establish the precise cause of
the earlier locale access violation; the debugger did not reproduce that crash.

The coordinator now waits for agent API readiness and lets the native UI start
its own tray. It never launches a competing tray. A separate port-guard bug was
fixed with `SO_REUSEADDR`: recently closed connections must not be mistaken for
an active listener. The failed guard run is retained separately in the evidence.

The non-debugger `desktop-tested-sequenced-smoke2-20261012` run passed:

- G502 X PLUS and G915 X LIGHTSPEED TKL both reached `ACTIVE` with resources.
- All five startup read-only requests succeeded. Profile/G-Shift reads succeeded
  again after another 35 seconds of combined agent/UI operation.
- Inventory showed one agent, one main UI and one native background tray, plus
  updater/service and Electron child processes.
- The launch log contained no unhandled-page-fault record.
- The scoped Quit action exited zero and left no processes in the prefix.
- All 36 checked files passed post-run integrity validation. Desktop validation
  passed. Automated execution used a private Xvfb display only.

This is one successful integrated run, not long-duration stability or visual
acceptance. Earlier failures remain archived. Headless EGL and optional WinRT
activation errors remain in logs; they are not new confirmed blockers without
corresponding failed behavior.

## Follow the existing agent implementation

Agent SHA-256:
`61772de356096af7cea986e2f9b3f42db69e6f410c4106609998c389fd755fc1`.
Addresses below use preferred image base `0x140000000`.

| Evidence | Observed behavior |
| --- | --- |
| Installed `resources/app.asar`, `app.min.js` | GET/SUBSCRIBE `/input/gshift`; SET uses `{on: boolean, source: "HOST"}`. |
| `FUN_1419b2570` | Registers `/input/gshift` with handler `FUN_1419b2280`. |
| `FUN_1419b2280` | Unpacks `logi::protocol::devices::GShift`, locks state, updates the on/off flag and recalculates assignments. |
| `FUN_1419a6fd0` | Walks assignments and resolves shifted/unshifted slots. |
| `FUN_142574080` | `SlotHelper::setShifted`, with slot-category validation. |
| `FUN_1419a59c0` | Device-event G-Shift route and its explicit failure reporting. |
| `FUN_141a6f8a0` | `logi::siminput::virtual_input_win::do_send_input` builds INPUT data and calls Windows `SendInput`. |
| `FUN_1424cb400`, `FUN_1424cc0b0` | Hook/raw-input registration points. |

The corpus contains actual `SendInput` calls, not merely protobuf objects with
that name. This is an output route to test before deciding another driver or
remapper is needed. The complete runtime chain from physical G-Shift input
through these functions has not yet been demonstrated.

Confirmed GET routes are `/devices/list`, `/profiles`, `/profile/active`,
`/profiles/auto_switching`, and `/input/gshift`. The copied installation reports
one default profile; the active ID agrees with it. G-Shift and automatic switching
return protobuf defaults (false fields omitted). A trial GET `/profile/persistent`
returned INVALID_ARG, specifically no GET handler; it was removed from the probe.
Earlier guesses `/profiles/list` and `/profiles/current` were also incorrect.

`agent_snapshot.cjs` queries only the five confirmed GET routes, preserves all
responses/errors, and exits reliably if the agent stalls or disconnects. It uses
the installed Node's built-in WebSocket without packages. Its ten-second
transport timeout applies to a machine request, not the user's testing session.

## Coverage and remaining work

Read-only route validation: **5/5 (100% of the selected routes)**. The next
functional acceptance checklist has **0/4 fully verified behaviors**:

1. Physical G-Shift press/release selects and releases the alternate assignment.
2. That assignment's action reaches a native Linux application.
3. Edited assignments/profiles persist through G HUB restart.
4. Profile selection works, including automatic switching on Linux focus changes.

These denominators describe validation, not overall implementation percentage.
The agent already contains the mechanisms; the remaining Wine gaps cannot yet
be counted. No physical bindings were changed, profiles imported, or input
injected into the live desktop. The user can test and save snapshots at leisure.
On failure, trace the specific boundary above instead of repeating bulk analysis.

Evidence, including failures, debugger source, API snapshots and selected
decompilations, is archived at
`/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/desktop-agent-profiles-20261012/`.

## 2026-10-17: ordinary mouse assignments and host keyboard output

The user reports that mouse macros work but ordinary key assignments do not.
Read-only queries to the running agent confirmed ACTIVE G502 X PLUS and G915 X
LIGHTSPEED TKL devices with onboard mode disabled. The active Desktop profile
still contains the mouse assignments: G7 `[`, G8 `]`, G9 `0`, G4 right Control,
G10 Delete, G5 a user macro, and G6 G-Shift. No profiles or bindings were changed.
The restarted Ghidra instance was found and reconnected to `ghub2remapper`;
existing DragonShard evidence was sufficient for this investigation.

`FUN_141a6f8a0`, in the same SHA-pinned agent above, builds `INPUT_KEYBOARD`
packets from HID usages and calls `SendInput(1, &input, 0x28)`. Its translation
supplies both virtual key and scan code, plus extended/release flags. The
previous private Wine `NtUserSendInput` enqueues these into wineserver. A
private-display reproduction returned success for all 22 synthetic packets but
delivered **zero** events to the focused native X11 window. That establishes an
output boundary defect independently of the physical mouse-button path. The
reason the user's particular macro works has not been established; firmware
output is one possible different path, not a measured conclusion.

Patch `0043-ghub-host-keyboard-output.patch` routes ordinary keyboard packets
through the existing X11 keyboard translation and XTEST extension. It requires
both `WINE_GHUB_HOST_KEYBOARD=1` and an executable named `lghub_agent.exe`.
An internal GDI escape connects matched Win32u/X11 libraries without changing
the driver ABI. It does not also enqueue the packet into wineserver: X11
delivers it to whichever native or Wine window has focus. This avoids duplicate
keystrokes when the target is another Wine program.

The translation distinguishes right Control, dedicated navigation keys, and
main/keypad Enter. It refuses absent F13–F24 mappings rather than activating
an unrelated XF86 desktop command assigned a spare virtual key by Wine.
Unsupported mappings or a missing XTEST backend return a failure/partial input
count. Unicode-packet input, mouse output, Wayland support, and every possible
keyboard layout are outside this patch's validated scope. No host packages,
system Wine libraries, firmware, or hardware mappings were altered.

`build_private_host_keyboard.sh` rebuilds only the two Unix libraries and writes
a version marker. The prepared launcher requires their hashes and marker to be
pinned together before enabling the route. `prepare_tested_desktop.py` retains
the marker in subsequent prepared installations.

Validation uses `host_keyboard_probe.c` (compiled with MinGW as
`lghub_agent.exe`), `host_keyboard_receiver.c` (linked with X11), and
`run_host_keyboard_test.py`. The latter runs under `run_private_build.py` on a
stopped disposable prefix, a fresh network namespace, and its own Xvfb. Physical
HID drivers are disabled. When dropping from root after `unshare`, use
`sudo -n -H -u firesongz`, not bare `setpriv` with root's USER/LOGNAME/HOME.

| Check | Result |
| --- | --- |
| Previous libraries, focused native receiver | 22 SendInput successes, 0 native events: reproduced defect. |
| Patched libraries, focused native receiver | 22/22 exact events, including Control chord state and release. |
| Patched libraries, focused Wine receiver | 22/22 exact messages, virtual keys and scan/extended/release bits; zero duplicates. |
| Other executable with the environment flag | No native events; ordinary Wine behavior retained. |
| Agent-named probe without the environment flag | No native events; opt-in enforced. |
| Launcher/device policy unit checks | 5/5 passed. |
| Native tray, private KWin/X11, two vertically arranged monitors | 171 checks, zero failures; placement, cursor, dismissal, four navigation rows and Close. |

Evidence is under `wine-lab/codex-run/key-bindings-20261017/`. The passing runs
are `baseline2`, `patched3`, `wine-target2`, `other-process`, and
`disabled-bridge`. Each has `event-validation.json` asserting exact captured
events. Earlier failures remain available: an initial executable-name comparison
incorrectly folded underscores, navigation selected keypad counterparts before
extended-bit disambiguation, and the initial Wine receiver lacked real X focus
without a window manager. These were fixed before the passing runs.

The broader tray test caught test-prefix damage from the initial `wineboot -u`
attempt: it reset the ApiInformation and DataWriter activation paths to stock
`wintypes.dll`, and replaced the copied Shcore DLL. After Shcore was restored,
both old and patched libraries still produced agent exit 3. The final exception
was `winrt::hresult_not_implemented`; enabling fixme output identified stock
`api_information_statics_IsPropertyPresent` as the failure. Comparing activation
registrations found exactly the two changed paths. Restoring the existing
ApiInformation proxy and private Windows.Storage registrations repaired this
test fixture. Root's inherited environment also selected an unwanted test user
profile and was corrected. The live prefix retained its correct registrations
throughout. These failures and old/new comparison traces remain archived; do
not use `wineboot -u` to start future keyboard probes in a prepared prefix.

The final tray run is `tray-regression3`. After it passed, the exact tested
Win32u/X11 libraries, marker, and launcher were installed atomically into
`desktop-tested-gesture-20261014`; its manifest now verifies 48 files and enables
the agent-only output route. Previous files and manifest are retained under
`key-bindings-20261017/desktop-before`, with hashes and deployment details in
`deployment.json`. The live agent's PID/start time stayed unchanged. Existing
processes retain their old library mappings; the change takes effect after the
Desktop launcher's **Quit this G HUB session** action and a fresh launch.

The five selected routing checks are **5/5 complete**. This is not 100% mouse,
G-Shift, or G HUB compatibility: physical button → agent → native application
acceptance remains unverified. The broader four-behavior checklist above remains
open. The next useful evidence is a user-operated test of the existing mouse
bindings after a clean launch, not another whole-binary decompilation.
