# CachyOS: native tray absent from the real X11 session

## Confirmed cause and repair

**Owner acceptance, October 27:** after relaunch, the owner clearly sees the
CachyOS panel tray icon and requested moving on to Wayland. This closes the
reported missing-icon symptom. It does not independently certify every menu
action or the physical-device feature matrix.

The packaged Wine 10.20 automatically selects kernel synchronization when
`/dev/ntsync` is accessible. CachyOS exposes that device; both the original
workstation and the Manjaro VM did not expose it when checked. The live
CachyOS wineserver held `anon_inode:ntsync` descriptors. This changed runtime
behavior despite identical Wine binaries, DLL hashes and feature flags.

The failing native tray raises `0xe0464645` in
`CoreMessagingXP.dll+0x28132` before initializing the graphics stack. A bounded
debugger in a disposable prefix identified `MessagingValidationException::FromResult`
with result **9**, reached from `WaitCollection::UnregisterWait`. The registrar
attempted to unregister its shutdown wait twice. This locates the failure;
it does not claim a complete repair of Wine's NTSYNC implementation.

A controlled comparison establishes the relevant boundary:

| Same CachyOS host and packaged application | Frontend checks | Native tray checks | Result |
| --- | --- | --- | --- |
| Private Xvfb, NTSYNC exposed, old launcher policy | 4/4 | 0/2 | Same `0xe0464645` crash; overall failure |
| NTSYNC exposed to outer test namespace, corrected application boundary | 4/4 | 2/2 | 6/6; one XEmbed dock request; tray alive |
| Newly prepared installation, final automatic host-NTSYNC challenge | 4/4 | 2/2 | 6/6; mask present; one dock request; tray alive |

The repair is `tools/packaging/server_sync.py`. The desktop launcher and test
overlay use it before starting Wine. If NTSYNC exists, an anonymous empty file
with mode `000` is mounted read-only over that one device **inside the
application namespace**. Wine's open fails and it selects its existing server
synchronization path. A worker check refuses to proceed if NTSYNC is still
accessible. Using `/dev/null` would be incorrect: its successful open would
make Wine attempt unsupported NTSYNC ioctls instead of taking the fallback.

The host device remains accessible with unchanged inode, mode and device ID.
No module is unloaded, no host permission is changed, and no other Wine
application is affected. Graphics, HID and X11 access remain available.
On a host without NTSYNC, the boundary passes through unchanged. This policy
preserves the synchronization backend used for the working installation;
NTSYNC performance is not qualified or promised for this pinned runtime.

The regular UI gate now exposes host NTSYNC to its outer test namespace when
available, then exercises the production mask. It can no longer accidentally
avoid this compatibility test merely by having a synthetic `/dev`.

Packaging tests: **91/91** pass, including a real nested-bubblewrap test that
checks denied access to the synthetic NTSYNC device, retained `/dev/null`
access, and preserved standard input. The deployed real-session container also
passed a non-GUI check of the mask and retained graphics-import search paths.
A separate, bounded native startup diagnostic with NTSYNC masked survived
60 seconds without access violations. Live panel presence and menu clicking
after the user's normal restart remain acceptance work.

## Established findings

The owner reported no G HUB panel icon after entering Plasma X11. Read-only
inspection found the agent and Electron interface running, but no
`lghub_system_tray.exe`. Plasma's `plasma-xembedsniproxy.service` was active and
the X11 tray selection had owner `0x1c00007`. The StatusNotifier watcher existed;
its only registered item was unrelated to G HUB. These findings locate the
missing component before panel presentation, rather than establish a missing
Plasma package.

This physical machine has a GeForce GT 730 (GK208B) and NVIDIA 470.256.02.
An offscreen GLX pixmap/context probe, run through the packaged container,
successfully returned NVIDIA GeForce GT 730 and OpenGL 4.6.0. It created no
desktop windows and sent no input. The earlier driver check only called
`dlopen`; this context probe provides stronger evidence but still does not
exercise the complete native Direct3D/XAML tray path. Mesa EGL warnings in the
live log do not, by themselves, prove the cause of the missing tray.

An isolated software-rendering test on the same CachyOS machine started the
native tray and observed icon registration. Thus a system-wide missing DLL or
uniformly broken package cannot explain all observed runs. The live native
tray's startup/exit cause was unresolved at that initial checkpoint; the NTSYNC
comparison above resolves it.

## Why the original diagnostics missed it

The pinned Electron main process starts the native tray with `--background`,
`detached: true`, `windowsHide: true` and `stdio: "ignore"`. Wine also suppresses
debug output when stderr points to `/dev/null`. Consequently, enabling Wine
logging in the parent alone cannot recover the discarded child diagnostics.

The old four-check UI gate verified installed files, bundled fonts, the default
profile and readable first-run text. It disabled GLX and had no tray host. It
explicitly excluded tray acceptance, but the handoff still relied too heavily
on its pass result and artifact parity when predicting real-session readiness.
The earlier tray-font test proved one prerequisite on software rendering; it
did not qualify the legacy NVIDIA/native-XAML combination. That was the research
and validation blind spot, not evidence that the historical tray patches were
lost or that all those patches must be rewritten. The additional blind spot was
the kernel interface exposed by the real container: pinning Linux userspace
does not pin the host kernel or the Wine synchronization backend it enables.

## Installer and validation changes

- `prepare_frontend_startup.py` now preserves native tray stdout/stderr in the
  launch log, enables error-level Wine diagnostics for that child, and reports
  its exit code/signal. The transformation checks the pinned `main.js` hash,
  preserves vendor launch arguments and singleton/lifecycle ownership, and
  regenerates ASAR integrity metadata. It does not add a competing tray or an
  unconditional crash-restart loop. Fonts and the prior picker patch remain.
- `linux_ui_probe.py` now enables GLX on its private Xvfb and runs a minimal
  XEmbed host, with separate checks for native-process survival and a dock
  request. `tray_registration.py` refuses normal-host execution and refuses to
  replace an existing tray selection owner. This small test host is not shipped
  as a replacement for Plasma's panel integration.
- No host package or graphics-driver changes were made. No live desktop input
  was injected. The existing CachyOS installation was not replaced while running.

Measured validation:

| Test | Result |
| --- | --- |
| Corrected candidate on private CachyOS Xvfb | **6/6**, one dock request, native tray running |
| Negative control: native tray executable disabled | Original **4/4** pass; new **0/2** pass; overall exit **1** |
| Packaging unit suite | **87/87** pass |
| Transformed real vendor archive | **267/267** packed entries checked; modified JS syntax passes |
| Live CachyOS tray icon recovery | **Owner confirmed**, October 27 |
| Every live CachyOS tray menu action | Not separately confirmed by the icon report |

The source changes apply to future preparation. The separate validation source
snapshot is `~/Source2/Penguin-LGHUB-cachyos-tray-20261027/` on CachyOS. The
existing Desktop entry keeps its installed prefix and settings. Its source
snapshot, `~/Source2/Penguin-LGHUB-cachyos-6cce5ca/`, now includes the validated
synchronization boundary. It takes effect after a full quit and relaunch;
reopening an already-running session cannot replace its wineserver backend.
Future prepared archives also include the native-child logging change. The
running installation's application archive was not rewritten during this repair.

The temporary **Diagnose tray** shortcut initially failed because its helper
was outside the container's mounted installation directory. That was our
diagnostic packaging error. Moving the helper into `runner/` enabled capture
of the actual crash. The temporary shortcut is now retired into
`desktop-20261027/runner/retired-tray-diagnostic.desktop`; only the main launcher
remains on the Desktop. Diagnostic logs remain under `desktop-20261027/logs/`.

Private controlled evidence beneath the same validation directory:
`tray-20261027-001/`, `tray-20261027-002/`, and
`tray-20261027-negative/`. The first diagnostic explicitly starts a second
tray for investigation, so it is not singleton/lifecycle acceptance evidence;
the final positive gate uses the frontend's natural tray launch only.
NTSYNC comparison evidence is in `tray-ntsync-exposed-001/` and
`tray-ntsync-masked-001/`. The negative control's frontend alone passes; its
final report correctly fails both native-tray checks. Intermediate observer
JSON is not the final combined test result.
`tray-ntsync-fresh-002/` is the final independently prepared positive gate;
its report records `wine_synchronization: server` and
`ntsync_mask_present: true`.

## Benefit to the Manjaro path

The new gate prevents a fresh Manjaro installation with a dead native tray from
passing as a healthy UI installation. Persistent child errors and exit status
also make future regressions diagnosable there. These are concrete validation
and support improvements; no speedup or visible improvement to an already
working Manjaro tray has been demonstrated. The synchronization repair also
prevents this failure on a Manjaro installation that gains an accessible NTSYNC
device. The tested Manjaro VM currently has kernel 6.12.77 and no such device;
its already-working tray does not gain a visible feature from this policy.
