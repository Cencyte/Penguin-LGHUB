# Fresh desktop: tray fonts, launcher icon, and restore investigation

> Historical investigation record. For current installation, X11/Wayland profiles and validation status, see the [installation guide](release-installer.md) and [recorded environments](../../packaging/tested-environments.json).

The fresh Manjaro VM exposed a missing native-tray prerequisite after the
four-check frontend startup gate passed. That gate did not test the tray or
minimize/restore. It must not be treated as desktop acceptance.

## Tray startup: reproduced and repaired

Without the workstation's Windows fonts, the native XAML tray fails fast with
`0x8000ffff` before registering its icon. The captured native stack includes
`CCompositeFontFamily::DetermineDefaultLineMetrics`,
`CFontFamily::EnsureCompositeFontFamily`, and `CTextBlock::GetLineHeight`.
DirectWrite tracing shows repeated requests for the absent `Segoe UI` family.

The recipe now adds this per-prefix replacement:

```reg
[HKEY_CURRENT_USER\Software\Wine\Fonts\Replacements]
"Segoe UI"="DejaVu Sans"
```

Wine's DirectWrite replacement mechanism applies when the requested family is
absent. DejaVu Sans already ships in the verified Linux runtime; this change
does not copy Windows fonts or change the host's fonts. It is separate from the
earlier Electron fix that explicitly loads G HUB's ten bundled font faces.

The fresh-prefix registry includes the replacement. The desktop launcher also
applies it before starting G HUB, repairing installations made with the older
recipe on their next launch. A running session is not changed underneath the
user.

Controlled evidence, using disposable prefixes and private Xvfb:

| Condition | Native result |
| --- | --- |
| No replacement | Font-metrics failure; process exits before icon registration |
| Segoe UI → DejaVu Sans | `Shell_NotifyIcon` registration returns success; tray survives the 60-second observation; no access violation |

This establishes the startup repair. It does **not** certify Plasma panel
placement, every menu action, symbol glyphs, other languages, or device behavior.
Those remain separate acceptance checks.

## Launcher and configuration parity

Generated `.desktop` entries now use a copy of the project logo kept beside the
installation's configuration. The VM's existing entries were updated as well.

The preflight now checks required compatibility environment flags and DLL
overrides as well as file hashes. In particular, a leftover laboratory override
disabling `winebus.sys` is rejected. Update freezing remains scoped to G HUB.

## Black window after restore: still open

The earlier lazy retained-surface and X11 image-flush fixes are present in the
VM. The actual loaded modules match the working workstation and packaged runtime:

| Module | SHA-256 |
| --- | --- |
| `win32u.so` | `33a3c8758866ff30d87ba12c4ce101eb9def2da6e0476fb4e5c6713e99625831` |
| `winex11.so` | `ed5b32b12f62123614858edd86647aa287571aed1d0d750960dbad4734b9bc4f` |

The required behavior flags also match. Consequently, the reported symptom
cannot currently be attributed to accidentally shipping the old unpatched
renderer. A matching symptom is not proof of a matching cause.

Private tests exercised the actual G HUB interface, not just DOM readiness.
They queried native minimized/restored window state and captured XComposite's
displayed pixels. KWin 6.5.5, Mesa software rendering, and a 1280×800 display
match the important VM desktop characteristics. Three taskbar-style cycles on
the Devices screen retained the visible navigation and “Connect Your Logitech
Gear” text, both with and without the font replacement. Further cycles restarted
the frontend without the remote-debugging launch switches and also retained the
image. The reported all-black failure has **not** been reproduced or fixed by
these tests.

The first isolated window-manager attempt lacked GLX and therefore compositing;
it is not compositor acceptance evidence. Setting the private Xvfb's Mesa driver
search path correctly enabled compositing for the subsequent tests. No host
packages, compositor configuration, or live desktop input were changed.

## Three independent release gates

1. **Artifact and configuration parity:** exact native files, patched Wine
   modules, activated frontend archive, registry prerequisites, behavior flags,
   and update policy. A file inventory alone misses configuration dependencies.
2. **Behavioral regression checks:** startup, text, native tray registration,
   popup interaction, and minimize/restore with final displayed pixels. Passing
   startup is insufficient; a renderer's DOM or screenshot alone cannot certify
   the host compositor's output.
3. **Real-session acceptance:** user-driven Plasma tray/menu, graphics-driver,
   device, binding, macro, lighting, profile, and G-Shift checks on each target.
   A VM without passed-through peripherals cannot qualify device functionality.

Packaging unit tests: **84/84 passed**. Live acceptance of this tray repair and
resolution of the reported black-window failure remain outstanding. This is
not a whole-project completion percentage.

### Subsequent owner feedback

After restarting the VM installation, the owner reported that the black window
was fixed and tray integration seemed fine. This is positive live-session
acceptance of those two observed behaviors on the Manjaro VM. The precise cause
of the earlier black window remains unproven; no new renderer patch was applied.
Physical-device behavior in the VM and CachyOS acceptance are separate gates.

## Local evidence breadcrumbs

On the validation VM, below `.local/state/penguin-lghub-validation/`:

- `desktop-trace-002/`: failing native stack and registration negative control.
- `desktop-font-002/`: successful registration with the replacement.
- `wm-restore-003/` and `wm-restore-004/`: composited main-screen comparison.
- `wm-restore-005/` and `wm-restore-006/`: frontend restart without debugging.

Diagnostic source snapshots are retained in the development workspace's
`wine-lab/codex-run/20261027-vm-desktop-diagnostics/`. They are investigation
harnesses with VM-specific paths, not a portable installation requirement.
No prefix, personal settings database, vendor DLL, or screenshot is included
in this source report.
