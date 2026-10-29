# Wayland: preserve the Wine work, validate the host boundaries

Investigated October 27, 2026, after the owner confirmed the repaired CachyOS
tray icon. This is a research and feasibility result, not a Wayland release.

Follow-up: the [packaged-Wine experiment](2026-10-27-wayland-packaged-wine.md)
now validates the 22-event keyboard fixture on both Manjaro and CachyOS,
including separate pending-permission controls. The original experiment below
remains the narrower native-XTEST result; it has not been relabeled as G HUB.

The working X11 launcher, prefixes, desktop sessions, package versions and
device permissions were not changed. No input was injected into a live desktop.

## Decision

Start with **our pinned Wine 10.20 using its patched X11 driver on the host's
Xwayland**, initially on KDE Plasma. Keep the packaged Linux runtime and the
new server-synchronization policy. Test the existing keyboard bridge before
building a replacement input service or porting all driver fixes to native
Wine Wayland.

The key correction to the earlier research is that **XTEST is not necessarily
confined to X11 clients on modern Wayland systems**. Xwayland can translate it
into libei input, accepted by the compositor and delivered to native clients.
This is established by upstream source and by the isolated test below. A
container by itself does not provide that capability; the host's compositor,
Xwayland build and authorization path do.

Expected first architecture:

`Logitech HID -> G HUB agent -> patched Wine SendInput -> XTEST -> host Xwayland -> libei -> KWin -> focused native Wayland/Xwayland application`

This complete chain remains a hypothesis until the packaged Wine and actual
peripheral tests pass. The XTEST-to-native-Wayland portion is now measured.

## Measured host capabilities

Read-only SSH inventory, with both hosts still in their existing X11 sessions:

| Component | CachyOS physical machine | Manjaro VM |
| --- | --- | --- |
| KWin / Plasma | 6.7.5 | 6.5.6 |
| Xwayland | 24.1.13 | 24.1.9 |
| libei | 1.6.0 | 1.5.0 |
| xdg-desktop-portal | 1.22.1 | 1.20.3 |
| KDE portal backend | 6.7.5 | 6.5.6 |
| Xwayland links libei and exposes `-enable-ei-portal` | Yes, binary checked | Yes, binary checked |
| Native Wayland Qt platform plugin | Present, used by the probe | Not probed in this pass |
| End-to-end G HUB on Wayland | Not tested | Not tested |

Do not require a package named `qt6-wayland` merely by name: on the current
CachyOS installation the native Qt plugin and development metadata exist even
though that separate package name is absent. Capability checks are stronger
than a hardcoded package-name checklist.

### Two permission paths, not one

KWin **6.5.6** launches Xwayland with `-enable-ei-portal` by default. Its direct
EIS socket path is selected if `XwaylandEisNoPrompt` is enabled. We must not
enable that global permission bypass as an installer shortcut.
Sources: [6.5.6 Xwayland launcher](https://github.com/KDE/kwin/blob/v6.5.6/src/xwayland/xwaylandlauncher.cpp),
[6.5.6 EIS backend](https://github.com/KDE/kwin/blob/v6.5.6/src/plugins/eis/eisbackend.cpp).

KWin **6.7.5** gives its own Xwayland a private `LIBEI_SOCKET` connection. It
checks the connecting process and prompts for the X client's claimed name,
unless that name has previously been allowed. The prompt offers to remember
the choice, with revocation in **System Settings -> Legacy X11 App Support**.
The name is not a cryptographically authenticated application identity.
Sources: [6.7.5 EIS backend](https://github.com/KDE/kwin/blob/v6.7.5/src/plugins/eis/eisbackend.cpp),
[connection authorization](https://github.com/KDE/kwin/blob/v6.7.5/src/plugins/eis/xwaylandeiscontext.cpp),
[permission prompt](https://github.com/KDE/kwin/blob/v6.7.5/src/plugins/eis/eisprompter.cpp).

Xwayland derives the name from the X client's process command. We still need
to measure which name the packaged Wine agent presents. Preserve the runtime's
existing shared PID namespace so host-side client identification remains
possible. Do not pass KWin's private EIS socket into Wine or connect directly
to it: the normal client-facing boundary here is the authenticated X11 socket.
Source: [Xwayland 24.1.13 XTEST implementation](https://gitlab.freedesktop.org/xorg/xserver/-/blob/xwayland-24.1.13/hw/xwayland/xwayland-xtest.c).

The independent portal route uses `CreateSession`, `SelectDevices`, `Start`
and `ConnectToEIS`. A portal implementation may grant persistence and return
a replacement restore token; it is not permission for us to silently approve
the dialog. This is the fallback architecture if the existing bridge proves
unreliable. [RemoteDesktop API](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html).

## Feasibility experiment: actual native Wayland key delivery

Added `tools/packaging/wayland_input_probe.py` and two small native fixtures.
They compile using existing host development tools and run a fresh KWin with
virtual output and software rendering. Bubblewrap creates private network,
PID, IPC, `/tmp`, `/run` and device namespaces; home, configuration and D-Bus
are also private. There is no physical input device or host GPU in the test.
Even its `DISPLAY=:0` is a private socket, not the live desktop's display.

The receiver requires Qt's **wayland** backend and records symbols, scan codes,
modifier state and repeat state. The sender uses the same XTEST API as patch
0043, but is a small native fixture rather than Wine or Logitech's agent.

| Controlled case | Measured result | Interpretation |
| --- | --- | --- |
| Only the test sender preapproved in the disposable KWin configuration | **8/8 exact events**: A down/up, right-Control+A chord with releases, F12 down/up | The proposed host-side transport reaches a native Wayland window; Control ends released |
| Same stack with no preapproval | **0 events**, KWin's authorization prompt process present | Merely submitting XTEST does not establish authorized native delivery |

Both final controls pass: **2/2 (100% of this narrowly defined experiment)**.
Permission acceptance by a person, denial, revocation, session restoration,
layout changes, held keys across disconnect and desktop shortcuts are not yet
covered. The approved control uses a test-only name allowlist, not a simulated
click on an authorization dialog. The unapproved control tests a pending
prompt, not the result of clicking Deny.

Important finding: the unapproved sender still returns success from XTEST.
Our Wine patch treats successful event submission as `SendInput` success.
Therefore a successful API return alone cannot certify delivery on Wayland;
validation must observe the target receiver, and the launcher must explain
pending permission instead of displaying a false success.

Evidence: [result and source hashes](wayland-input-feasibility.json).
Full logs on the CachyOS host are under
`~/.local/state/penguin-lghub-validation/wayland-eis-approved-final/` and
`wayland-eis-unapproved-final/`. Earlier numbered attempts document harness
bring-up (Qt PIC compilation, private X socket directory and KWin wrapper
environment); they were not successful G HUB tests.

Reproduce on a host with the existing KWin 6.7 toolchain, using new output paths:

```sh
python3 tools/packaging/wayland_input_probe.py --output /path/to/new-approved
python3 tools/packaging/wayland_input_probe.py --output /path/to/new-unapproved --control unapproved
```

The current preapproval/prompt fixtures target **KWin 6.7**. They are not a
general compositor certification tool and do not model the 6.5 portal dialog.

## Six feature boundaries and what each needs

| Boundary | Reusable implementation | Wayland-specific obligation |
| --- | --- | --- |
| UI and graphics | Pinned Wine, native DLLs, fonts, frontend repairs, host graphics import | Run the X11 driver through Xwayland; test restore, resize, hover, palette dragging, scaling and multi-monitor presentation |
| Tray | Native G HUB tray, composition patches, server synchronization, Plasma XEmbed-to-SNI proxy | Verify dock request **and** visible icon **and** actual menu actions/placement on Wayland; an icon alone is not full menu acceptance |
| Keyboard output, macros, G-Shift | Agent assignment engine; patch 0043 keyboard translation and XTEST output | Qualify native targets, permission lifecycle, chords, repeat, release, focus changes and no duplicates; inspect non-keyboard/Unicode macro routes separately |
| Device access and reconnect | Existing Wine HID access, narrow device policy, namespace sharing | Verify active-seat permissions after Wayland login, unplug/replug and receiver modes; virtual output testing proves none of this |
| Profile selection and automatic matching | Agent profile storage/API and manual profile selection | Native Wayland foreground-app identity needs a compositor-aware source; test manual switching separately from automatic app matching |
| Lighting | Device communication and ordinary configured effects | Screen-sampled effects need native desktop capture through a consented ScreenCast/PipeWire path; ordinary lighting is a separate test |

The current [keyboard patch](../../patches/wine/0043-ghub-host-keyboard-output.patch)
is agent-only and opt-in, routes non-Unicode keyboard packets, and deliberately
avoids duplicate wineserver injection. It does not cover every mouse or Unicode
macro. Historical acceptance of selected macros does not identify every output
route; see the [original keyboard investigation](../dragonshard/2026-10-12-desktop-agent-profiles.md).

Plasma already bridges XEmbed icons into StatusNotifierItems, with activation
and context-menu handling. Reuse that bridge instead of inventing a second G
HUB tray. [Plasma proxy source](https://github.com/KDE/plasma-workspace/blob/v6.7.5/xembed-sni-proxy/sniproxy.cpp).

For automatic profiles, a small optional KWin script could report
`workspace.windowActivated` and application identity to a host broker that
selects the corresponding G HUB profile. That is a proposed integration,
not a currently implemented feature. Its mapping, persistence and agent write
route need validation; it should not collect window-title text unnecessarily.
[KWin scripting API](https://develop.kde.org/docs/plasma/kwin/api/).

For screen sampling, use a granted PipeWire stream and decide where the agent
will consume those frames. Having a working ScreenCast portal does not by
itself make Wine's Windows capture API return the full native desktop.
[ScreenCast API](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html).

## Graphics: a real CachyOS limitation, not an inferred one

The physical CachyOS target uses a **GT 730 and NVIDIA 470.256.02**. Its previous
Wayland-session journal, dated October 8, records missing required Vulkan
extensions, failed EGL setup and Xwayland falling back to software rendering.
That session remained available before the user moved to X11; we did not
switch it back or change drivers during this investigation.

NVIDIA's 470 documentation lists prerequisites and limitations for accelerated
Xwayland. Its later 495 release added GBM support; KDE removed its EGLStream
backend in the Plasma 5.24 development cycle. These sources explain why a
modern Plasma session on a legacy driver is not a representative accelerated
Wayland baseline, but the actual journal is the evidence for this machine.
[470 Xwayland documentation](https://http.download.nvidia.com/XFree86/Linux-x86_64/470.256.02/README/xwayland.html),
[495 release notes](https://www.nvidia.com/download/driverResults.aspx/181274/en-us/1000/),
[KDE changelog](https://kde.org/announcements/changelogs/plasma/5/5.23.5-5.24.0/).

Use the VM for isolated protocol/UI checks and CachyOS for its actual host
integration, explicitly separating software-rendered behavior from accelerated
performance. A VM success cannot qualify a bare-metal graphics driver. Do not
upgrade the pinned workstation or replace this legacy driver as a hidden
prerequisite of Penguin-LGHUB installation.

## Alternatives already used by real projects

| Approach | Existing project evidence | Decision here |
| --- | --- | --- |
| libei plus desktop portals | [Deskflow](https://github.com/deskflow/deskflow#is-wayland-for-linux-supported) and [Input Leap development account](https://blogs.gnome.org/uraeus/2023/11/) | Established fallback: a small host input broker if Xwayland mediation is insufficient |
| Kernel virtual input | [Solaar's documented uinput rules](https://pwr-solaar.github.io/Solaar/rules/) and [Linux uinput API](https://docs.kernel.org/input/uinput.html) | Viable fallback; adds a privileged boundary and virtual-device lifecycle. Do not grant every process world-writable uinput or run all G HUB as root |
| Compositor-specific foreground identity | [Solaar's GNOME extension](https://extensions.gnome.org/extension/6162/solaar-extension/) | Confirms that automatic app matching is a separate concern; use a KDE adapter for our initial scope |
| Wine's native Wayland driver | [Wine 10.0 announcement](https://github.com/wine-mirror/wine/blob/wine-10.0/ANNOUNCE.md#wayland-driver) | Later investigation: availability does not port our winex11 composition/input patches, nor automatically solve global output |

If a broker becomes necessary, keep it outside the Linux runtime container,
authenticate its local client, negotiate only required devices, handle keymap
changes, release held keys on shutdown, and pause appropriately on lock/session
changes. InputCapture receives input; GlobalShortcuts registers actions.
Neither substitutes for the RemoteDesktop input-emission API.

## Small implementation increments and acceptance gates

1. **Packaged Wine input proof — next.** Extend the private KWin test with our
   existing 22-event `host_keyboard_probe.c` compiled as `lghub_agent.exe` and
   the actual packaged runtime. Disable physical HID and use a disposable
   prefix. Repeat with the bridge disabled/other executable and assert no
   unexpected delivery. Observe native Wayland and Wine targets separately.
   Check the app name in the real permission request and the pressure-vessel
   namespace boundary. This decides whether another input backend is needed.
2. **Opt-in KDE/Xwayland launcher.** Add explicit experimental selection,
   authenticated local Xwayland connection checks, backend/version diagnostics
   and understandable permission guidance. Keep Wine on its existing X11
   driver. Retain integrity hashes, update freeze, read-only application mount,
   host graphics import, HID policy and server synchronization. Preserve the
   existing X11 default; do not globally relax KWin permissions.
3. **UI/tray and real-device acceptance.** Validate window recovery, tray
   placement and every menu action, then bindings, macro types, G-Shift and
   device reconnect. Include key release across focus changes, permission
   denial/revocation, lock/unlock, restart and no duplicate events. Provide
   persistent user tests without response deadlines.
4. **Remaining integration features.** Implement automatic native-app profile
   matching and screen-sampled lighting after their separate contracts are
   measured. Expand to other compositors only after the KDE path is qualified.

Measured coverage: **6/6 identified boundaries researched** with a concrete
reuse/gap/validation decision; **2/2 host capability inventories** completed;
**2/2 narrowly scoped input controls** passed on CachyOS. Production Wayland
launcher support remains unimplemented; **0/2 target installations** have
end-to-end G HUB Wayland acceptance. These are different denominators, not a
claim of a combined completion percentage. Confidence is high in the tested
transport, moderate in reusing the whole Wine path until increment 1, and
unmeasured for physical-device behavior in a live Wayland session.

Tavily's research executable was available and authenticated, but the request
was rejected for the account usage limit. Research continued against primary
upstream code, vendor documentation and local measurements; no Tavily report
was produced or relied on.
