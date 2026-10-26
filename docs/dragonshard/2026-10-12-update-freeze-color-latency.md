# Chapter 15: stop the unexpected installer, measure the color picker

The user confirmed that the previous drag/redraw fixes improved desktop use,
then reported an installer error during normal launch, slow color-pad dragging,
and the need to preserve this exact G HUB version. These are two connected
update issues plus a separate performance investigation.

## Why an application launch displayed “G HUB Installer”

The running prefix's software-manager log gives the complete chain:

1. Normal startup invokes `lghub_software_manager.exe` in update mode.
2. It unpacks another copy under the user's temporary `ghub-*` directory.
3. That copy runs with `--update_temp --language=en-US`.
4. `AreFolderAccessPermissionsAdminOrSystem` rejects the directory's two ACL
   rules. The bootstrap exits with “The uninstaller or updater claims it was
   started from a safe directory but actually is not.”

The screenshot belongs to that shared installer/update bootstrap. It does not
mean the Desktop entry accidentally points at the standalone installer. The
application/agent can continue while that separate process displays the error.
The log identifies software-manager version `2026.6.1214.0`; the bundled frontend
release is `2026.6.974819`. This pass does not bypass its security check.

## Version preservation now has enforcement

The native preferences are in `C:\ProgramData\LGHUB\periodic_check.json`.
The frontend's checkbox sends `SET /updates/periodic_check` through the agent's
WebSocket API. Its reducer/saga is `updatesSetPeriodicCheck`; the route's payload
type is `logi.protocol.updates.PeriodicCheck`.

The live agent initially returned `checkPeriodically=true` and
`downloadAutomatically=true`. A native SET switched automatic downloads off,
then GET verified the result. An unexpected detail matters: SET leaves the
existing `checkPeriodically` value unchanged. The first tool run correctly
reported that its stricter two-field expectation was not met. The corrected
tool verifies the automatic-download opt-out and separately reports the
periodic-check state. Both before/after records are retained.

The existing Tested Wine launcher now applies three measures at each cold start:

- With its launch lock held and the prefix stopped, preserve a first backup and
  set both native preference fields false, leaving unrelated keys unchanged.
- Exclude `lghub_software_manager.exe` through Wine's loader policy. This applies
  to both installed and temporary copies; their names are identical. Explicit
  attempts return status `0xc0000135` (shell exit 53) before running the bootstrap.
- Bind-mount `Program Files/LGHUB` read-only inside this launch's private mount
  namespace. A write to an existing sentinel fails with `EROFS`; no global host
  mount or installed Wine package changes. User profile/settings directories
  remain writable. This complements the existing prelaunch component hashes.

The updater service is retained because it serves the update API and participates
in the application's existing startup path. Its service is alive in the passing
test. No updater-service deletion, global firewall rule or host-wide domain block
was necessary. This is a version freeze for this launcher, not protection against
another independent installer/prefix or an administrator deliberately replacing
files. Installation/repair through this frozen session is intentionally blocked.

These launch-time safeguards take effect the next time the user quits through
the Desktop action and starts it again. Only automatic downloads were disabled
in the already-running session; its mount namespace and loader policy were not
retroactively changed. Its process set was unchanged across deployment.

## Validation and corrected test assumptions

Owned Xvfb plus a separate network namespace allowed a second native agent/UI
session without competing for the user's port 9010. The test namespace had only
loopback, so downloading updates was impossible. The candidate disabled its
Wine USB/bus drivers to avoid competing for physical peripherals.

The final integration run passes **10/10 checks**: read-only application files,
blocked manager execution, both preferences false in the native API, successful
native SET/readback, updater-service survival, no software-manager process, no
installer dialog, passive monitor startup, clean shutdown and persisted policy.
The agent's existing five-route startup check also passes. This test does not
claim physical device/lighting acceptance. Earlier full application tests had
established ACTIVE peripherals; this pass deliberately avoided a second hardware
consumer.

Early harness failures were not application incompatibilities: sudo's restricted
PATH chose Node 20 without built-in WebSocket instead of the installed Node 22.
The agent was listening while the probe itself failed. Explicitly preserving the
test PATH fixed it; the launcher now detects missing Node WebSocket support before
starting Wine. Another test queried the update route during the frontend/tray
handoff before it was ready; bounded retries then received its real response.
Failed runs are retained. The final configuration also survived a repeated cold
start. Preference checks confirm that unrelated values and the original backup
survive repeated enforcement and malformed JSON is not overwritten.

## Color-pad measurement: a real throttle, not yet the full lag explanation

The actual `app.asar` contains two Saturation implementations with a hardcoded
50 ms throttle. Module 76659 was extracted with its original bundled React and
dependencies into a disposable Electron fixture. No vendor source is committed
to this repository; the preparer reads the user's installed copy. It cuts off
the application entrypoint, so no agent commands or peripheral writes occur.
It is not yet proven which copy the specific user-facing pad currently mounts.

Real X11 drag events were sent only to an owned Xvfb display. Each variant ran
three identical drags; X input at 125 Hz was coalesced to approximately 60
renderer pointer events per second. The fixture used the current private Wine
builds and G HUB's Electron runtime.

| Measurement | Original 50 ms component | Temporary 16 ms component |
| --- | --- | --- |
| Color callbacks per three-second drag after warm-up | 58 | 181 |
| Median callback interval | 50.2 ms | 16.7–16.8 ms |
| 95th-percentile callback interval | 65.7–65.9 ms | 17.3–17.4 ms |
| Median animation-frame interval | 16.7 ms | 16.7 ms |
| Final selected HSV | identical | identical |

Thus one concrete frontend bottleneck changes from about 19 to 60 color callbacks
per second, with roughly 67% lower median interval. This is **not** a claim that
the complete G HUB UI or physical lighting became three times faster. The first
baseline drag had cold-start long tasks up to 146 ms; subsequent baseline drags
had none. Neither fixture includes the application's full Redux updates, device
preview, agent transport or HID output.

The 16 ms experiment has **not** been applied to the live `app.asar`: multiplying
downstream state/device updates before measuring them might make the complete
application slower. Headless tests also need `--disable-software-rasterizer` to
avoid an EGL/DRI3 initialization failure on Xvfb. Testing without it failed to
reach renderer readiness; that result cannot be extrapolated to the user's real
NVIDIA/KWin display. The Desktop launch graphics flags remain unchanged.

## A user-paced full-application recorder

The existing Desktop entry now has **Launch with color-picker performance
recording** in its right-click actions. Quit the existing Tested Wine session
first, then use that action and reproduce the lag whenever convenient. There is
no deadline for completing the interaction. Quit through the same Desktop Quit
action when finished.

The recorder attaches only after finding a G HUB process in this exact prefix
with its chosen loopback debugging port. It observes pointer-event age, frame
callback gaps and long tasks during left-button drags; it injects no input,
records no typed text or screenshots and sends no device commands. It writes
one result per released drag under the installation's `logs/` directory as
`*-color-performance.jsonl`. Ordinary launches do not enable this debugger or
recorder. The log-folder Desktop action exposes the results.

The recorder captured **3/3 fixture drags**, with roughly 180 pointer events
each, and also attached to the actual native frontend during the isolated
launch test. It measures renderer scheduling, not display scanout. Next, compare
these real-app metrics with the fixture: large long tasks point to application
work; healthy renderer timing with visible lag points further toward painting,
compositor presentation or physical-device output. That distinguishes the next
fix instead of guessing at Wine mouse handling again.

For the three reported concerns: the installer-popup prevention and version
freeze have implemented, isolated validation (**2/3 concerns**); the latency
investigation has measured component evidence and working instrumentation, but
the full-app lag remains unresolved. User-desktop acceptance of the new guard is
pending. These denominators describe this report, not overall G HUB completion.

All **41 pinned launcher files** verify after deployment; the application ASAR,
profiles and running application processes were preserved. Evidence and rollback
copies live at `/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/desktop-updates-color-latency-20261012/`.
SystemSage responded; free space remained about 54 GiB on ZFS and 442 GiB on
Captures. No dependency upgrades or live-desktop input were used.

## October 13 — user-operated color-chooser capture

At Cencyte's explicit request to start recording, the Tested Wine session was
restarted in recording mode; its previous launch lacked a debugging endpoint.
The passive recorder attached at 06:00:14 UTC. Cencyte performed the mouse
interaction himself. Two substantial released drags were saved, totaling
83.57 seconds and 4,326 pointer-move events. Navigation clicks are retained in
the raw capture but excluded from the following table.

| Metric | 5.35-second drag | 78.23-second drag |
| --- | --- | --- |
| Pointer-move events | 287 | 4,039 |
| Median / p95 event age at the observer | 3.1 / 10.5 ms | 3.0 / 11.7 ms |
| Maximum event age | 30 ms | 36 ms |
| Median / p95 animation-frame interval | 16.7 / 16.8 ms | 16.7 / 16.8 ms |
| Maximum sampled frame gap | 66.7 ms | 133.3 ms |
| Renderer long tasks | 3 | 57 |
| Median / p95 / maximum long-task duration | 75 / 87 / 87 ms | 69 / 77 / 82 ms |

The second drag reached the recorder's 4,096-entry frame-interval cap; its frame
statistics cover the beginning of that gesture, not every frame over 78 seconds.
Pointer-age and long-task arrays did not reach the cap. The user identified the
color chooser as the exercise target; the recorded DOM target is a DIV without
an ID, so the capture alone does not identify the specific bundled component.

These are measured renderer scheduling stalls during the trial. The data does
not identify their JavaScript stacks, establish the full cause of the reported
lag, measure color-state callback frequency, or measure final screen/hardware
latency. A focused renderer CPU trace during the same interaction is the next
useful diagnostic: attribute the repeated long tasks before changing the color
component's throttle or the downstream device-update rate.

After instructing Cencyte to release the button, the long sample was saved.
Only the exact recorder process was stopped through a verified pidfd; its
`stopped` record confirms closure. G HUB and its supervisor remain running.
No synthetic mouse or keyboard input was used. Raw capture, summary and hashes:
`/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/color-picker-user-capture-20261013/`.

External research included Logitech's [offline-use guidance](https://support.logi.com/hc/en-us/articles/360023368013-Can-I-use-G-HUB-offline).
The exact settings schema, process chain and fixes above come from this installed
version's source bundle, native API and local logs rather than generic update
advice.
