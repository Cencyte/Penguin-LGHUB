# Wayland launcher: missing installation depots

October 27, 2026. The owner reported a functional tray but a frontend stuck on
“Some required files are missing,” followed by the installation-damaged screen.
The cause is missing installation state, not evidence of a Wayland rendering
failure. Fonts were readable in the supplied screenshots.

## Evidence and failure in the earlier test

The delivered `wayland-desktop-001` prefix had **zero of 1,115 extracted depot
files** under `ProgramData/LGHUB/depots`. All 30 cached archives remained.
`current.json` had an empty version/build and depot list. The native updater's
`updater_sentinel.json`, written at 23:38:22 UTC, reported:

```text
depots_directory: Depots directory is empty: C:\ProgramData\LGHUB\depots
```

The verified acquisition candidate and working X11 `desktop-003` both still
contained all 1,115 files and the installed build `869589`. The pinned Electron
frontend's `CHECK_ESSENTIAL_FILES` handler tests that the depot directory exists
and is nonempty; the visible error is therefore accurate. It is not repaired by
suppressing that check or substituting a placeholder directory.

The depot directory's modification time was **19:28:01 VM local time**,
inside the final isolated smoke test and before the owner's 19:38 launch.
The previous test wrote into the prefix subsequently offered for user testing,
then certified only an agent, window, tray request and cleanup. It never called
the existing frontend readiness probe or checked depot integrity afterward.
The earlier report is retained with a link to this correction.

The updater remained enabled for application/service compatibility. The freeze
blocked scheduled downloads and the software-manager executable, and mounted
the application directory read-only, but left its updater-owned depot directory
and metadata writable. Those settings did not protect installation state against
local pruning/recovery. The exact native routine and trigger that emptied this
particular manifest are not yet isolated; directory timestamps and the sentinel
establish the loss and timing, not its internal call stack.

## Repair

- The desktop recipe includes all candidate-managed `ProgramData/LGHUB` files
  in its hash inventory, alongside the application and supplemental system DLLs.
  It rejects old inventories that certify only the application folder, missing
  depot files, and a modified installed-build manifest before starting Wine.
- The runtime mounts **both** the application folder and `ProgramData/LGHUB`
  read-only. This freezes depots, archive cache, installed-build metadata and
  update preferences. Wine registry, per-user profiles/settings, and the separate
  `ProgramData/LGHUBData` remain writable. The updater service remains available;
  its mutation of frozen installation state is no longer permitted.
- The equivalent isolated packaging sandbox applies the same installation-data
  protection, so headless tests exercise the policy used by the desktop runner.
- The new `wayland_launcher_probe.py` prepares a disposable installation and
  performs **two complete starts and shutdowns**. Each must pass the actual
  frontend's missing-file check, ten loaded fonts, Desktop/default profile and
  readable first-run prompt, plus tray registration, private runtime mapping,
  successful shutdown and unchanged installation hashes afterward.

The probe uses the production launcher/container/worker, adding a localhost
Electron debugging port only in its private network namespace to observe the
frontend. KWin, Xwayland, session bus, portals, `/tmp`, `/run`, PID namespace and
synthetic `/dev` are private. It neither sends input nor grants permissions.
It preserves a consistent user identity and never promotes its modified prefix
to the user's Desktop.

## Deployment and validation

The existing **Penguin LGHUB — Wayland test** shortcut now targets a freshly
prepared `wayland-desktop-002` under the VM's
`~/.local/state/penguin-lghub-validation`. Its source is
`~/Source2/Penguin-LGHUB-wayland-repair-20261027`. The broken `001` prefix,
settings and previous desktop entry are retained. The existing X11 installation
is unchanged. No host packages, desktop input permissions or system Wine were
changed in this repair.

Measured: **98/98 packaging unit tests**, including negative cases for deleted
depots and a reset manifest; **2/2 complete isolated Wayland frontend/restart
cycles**. Both cycles reached the readable first-run privacy screen, returned an
empty missing-files list, registered a tray icon and left all pinned installation
files intact. A separate X11 regression uses the same disposable test installation
with the existing Xvfb/renderer/tray probe. The machine-readable
[verification receipt](wayland-depot-repair-verification.json) records its result.

The owner confirmed the real Wayland tray works. The repaired frontend still
needs the owner's live desktop test; physical devices and full Wayland feature
acceptance are not claimed. Native Wayland application auto-profile matching
and screen-sampled lighting remain separate work.

The owner should close the old session using its tray menu, then reopen the
same Wayland Desktop shortcut and complete the normal first-run choices. The
new prefix is not automatically launched or clicked through by this repair.

Reproduce the isolated regression with an idle, verified runtime:

```sh
python3 tools/packaging/wayland_launcher_probe.py \
  --candidate /path/to/verified-candidate \
  --linux-runtime /path/to/idle-linux-runtime \
  --wine /path/to/private-wine --test-tools /path/to/pinned-test-tools \
  --output /path/to/new-disposable-test
```

The final `result.json` must report both cycles passing. Per-cycle frontend
receipts and screenshots are retained. A successful window creation alone is
never a passing frontend gate.
