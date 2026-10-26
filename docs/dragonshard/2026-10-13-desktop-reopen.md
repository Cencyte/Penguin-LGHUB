# Chapter 16 — A surviving agent must not block reopening the window

On October 13 the Desktop launcher reported that G HUB was already running,
but Cencyte could find neither a window nor a tray icon. Inspection confirmed
that the old supervisor (PID 3223393, started October 12 at 21:35), agent and
updater were alive in the Tested Wine prefix on display `:0`. Neither
`lghub.exe` nor `lghub_system_tray.exe` remained. This was not a stale PID file
or a window on the XRDP display. The launcher retained its lock while the agent
survived, then rejected every subsequent launch. Background survival was being
misreported as application accessibility.

The observed log does not establish why the original UI and tray exited.
No matching kernel OOM/segfault entry was found in the relevant interval.
This change fixes access to the surviving session; it does not establish that
all causes of tray termination are resolved.

## Let the native frontend handle another launch

The installed frontend already requests Electron's single-instance lock. Its
`second-instance` handler calls its window helper, which recreates a destroyed
window or restores, shows and focuses the existing one. The launcher now reaches
that path rather than blocking it.

The private Wine supervisor listens on a Linux abstract Unix socket named from
the installation path and user ID. A repeat click verifies the saved supervisor
identity (boot, PID start time, user, script and config), then verifies the socket
peer's PID/user/start time and submits a reopen request. The supervisor launches
the native frontend in its existing patched Wine environment. It keeps the
agent alive, preserves the version freeze and lets G HUB arbitrate its single
frontend. It does not assume that a running frontend necessarily has a visible
window. Requests from another display are rejected with the actual display
reported, rather than silently opening in the wrong desktop.

Performance recording survives reopen too: a replacement frontend gets a fresh
debugging port and passive monitor. Switching an already open normal frontend
to recording still requires the explicit Quit/recording launch sequence because
debugging cannot be enabled retroactively by a second-instance request.

The already running older supervisor cannot receive this new protocol. On the
user's next click, the launcher recognizes that older version and restarts
**only its own verified prefix**, provided both frontend and tray are absent.
It waits for clean shutdown and lock release before starting the replacement.
If the older session still has a frontend or tray, it asks for the existing
Desktop Quit action once instead of terminating a potentially active UI. The
one-time cold start also activates the prior chapter's full update freeze.

## Validation and remaining scope

The native integration fixture runs on its own Xvfb display and network
namespace, with Wine USB/bus drivers disabled. It cannot interact with the
user's physical devices or port 9010. It deliberately terminates only its own
test UI/tray processes to reproduce the reported background-only state.

The old-supervisor migration run passes **22/22 checks**, including a visible
replacement window, subsequent reopen without replacing the supervisor,
single-instance handoff, rejection of another display, clean Quit and retained
update protections. Five focused process-identity checks also confirm that
foreign commands, wrong start times, wrong boots, malformed state and missing
state do not result in signalling an unrelated process.

The final recording-mode run passes **23/23 checks**, including repeat clicks
preserving the original frontend and the passive monitor attaching again after
frontend/tray termination and reopening. Both native runs stop cleanly.

An initial test expected the window title `G HUB`; the actual native window is
`Logitech G HUB`. The corrected check also requires its X window to be viewable
and belong to `lghub.exe`, avoiding a false positive from the similarly named
tray window. A recording-mode test initially sampled the brief second-instance
process before its handoff completed; the harness now waits for that transition.
These unsuccessful test results are retained with the passing evidence.

This is bounded launcher validation, not a percentage of all G HUB functionality.
User-desktop acceptance of reopening remains pending; color-pad lag, physical
G-Shift and profile acceptance remain separate work. No live-desktop input,
window manipulation, process shutdown or dependency changes were used for this
repair. The existing Desktop entry continues to point at the corrected launcher.
All 41 installed component hashes verify after deployment. The old live
supervisor and its exact prefix process identities remain unchanged during
deployment; recovery begins only when Cencyte launches it again.

Evidence and rollback copies are under
`/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/desktop-reopen-20261013/`.
