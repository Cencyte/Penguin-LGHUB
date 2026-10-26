# Chapter 18 — Hover was correct; its border arrived late

Cencyte confirmed that the local color-picker fix works, then reported that the
G502 X PLUS and G915 X LIGHTSPEED TKL cards highlighted inconsistently. This was
another case where incoming input alone could have made a broken interface look
healthy to a diagnostic. The investigation therefore checked three separate
things: pointer delivery, computed hover styling, and pixels presented to X11.

## Isolation and cause

The installed frontend's `VB`/`tG`/`Wj` components render cards inside 3D flip
containers. The border itself is CSS: `.dls-Card:not([data-disabled=true]):hover`.
Its overlay and lighting underlay already have `pointer-events:none`. No agent
request is required to select the white border. A first isolated layout probe
found correct hover across nested children; flattening the flip containers made
no difference. There is no justification for changing that CSS or disabling the
card artwork/animations based on these observations.

Using the installed Electron runtime and original stylesheet, an inert fixture
reconstructs that card hierarchy without Redux, the agent, or hardware access.
Native XTEST motion is sent only to a newly created Xvfb display. The fixture
reports the matching DOM state while the host reads border pixels from that
same owned display. The initial test delivered all 63 pointer samples correctly,
but only **123/189** top-border samples matched the current state. The displayed
border often remained dark during hover and became white after the pointer had
left. This is a presentation failure, not a lost mouse-enter event.

The matching [Chromium software output implementation](https://raw.githubusercontent.com/chromium/chromium/142.0.7444.134/components/viz/service/display_embedder/software_output_device_win.cc)
paints through a window DC; its [Skia copy helper](https://raw.githubusercontent.com/chromium/chromium/142.0.7444.134/skia/ext/skia_utils_win.cc)
uses GDI bitmap copies. Wine's foreign-window path can issue those copies
straight to X11 instead of using its own retained window surface. The direct
`X11DRV_PutImage` and `xrenderdrv_PutImage` paths queued drawing requests without
flushing the completed window upload. Wine's event processing normally flushes
`gdi_display`, but a drawing worker need not pump another X event afterward.
[Xlib documents](https://xorg.freedesktop.org/archive/X11R7.5/doc/man/man3/XSync.3.html)
that drawing output is buffered and event-query functions normally cause it to
be sent. Waiting for unrelated activity therefore explains the stale final
image and why subsequent activity appears to disturb it.

## Repair and causal comparison

Patch **0035** explicitly flushes the completed image upload in both the XRender
and direct X11 paths when the destination is a window DC. Offscreen memory DCs
retain batching. It uses `XFlush`, without an `XSync` server round trip, polling,
forced DOM repaints, synthetic input in the real application, or a global
compositor change. The private X11 builder now reproduces patches 0033 and 0035.

An intermediate patch covering only `X11DRV_PutImage` still failed (122/189).
Adding the XRender path produced 189/189. A stronger regression samples all four
edges of all three cards while entering, traversing children, and leaving, over
three passes. All runs require correct native pointer coordinates, correct CSS
hover, the expected theme, and no renderer errors.

| Test | Matching border pixels | Other evidence |
| --- | ---: | --- |
| Original driver, expanded negative control | 488/756 (64.55%) | 63/63 input and CSS samples correct |
| Patched driver, grid cards | 756/756 (100%) | 63/63 input samples |
| Patched driver, list cards | 756/756 (100%) | 63/63 input samples |
| Patched driver, XRender unavailable | 756/756 (100%) | 63/63 input samples; server RENDER extension verified absent |

The fallback test initially used an unrecognized `UseXRender` registry setting.
That run passed, but did not establish fallback coverage and is excluded from
the table. The corrected test disables the RENDER extension on its own Xvfb and
checks the extension list. The original-driver negative control swaps only the
private driver and reliably restores stale presentation.

The earlier frameless-window regression also passes: four mouse drags release,
a stale mouse move command is ignored, keyboard move/resize work, and all 32
presentation samples survive movement, resizing and minimize/restore. A full
native G HUB candidate session passes **27/27** launch, reopen, recording,
update-freeze, mounted-archive and shutdown checks inside a loopback-only network
namespace with peripheral drivers disabled.

## Deployment, coverage and next user check

The tested Desktop installation has the rebuilt `winex11.so` and an updated hash
pin. It was replaced with a new inode: the existing live session still maps the
old driver, its supervisor identity is unchanged, and it was not interrupted.
All 42 manifest pins verify. The installed host Wine and the color-preview ASAR
are unchanged. The candidate installation also has the repaired driver.

Use the Desktop launcher's **Quit this G HUB session** action, then open
**G HUB (Tested Wine)** again. Hover across both peripheral cards, their titles,
images and buttons, then away. The border should follow the current card and
clear on departure. Closing just the frontend can leave the old Wine supervisor
running, so the explicit Quit action matters.

For this defect, implementation and the defined automated checks are complete:
**2,268/2,268** border-pixel comparisons, **32/32** window presentation samples,
and **27/27** native launcher checks. These denominators are measured coverage
of the stated tests, not a whole-program percentage. User-desktop acceptance of
this hover repair remains outstanding. The fixture does not reproduce physical
peripheral lighting or all vendor card variants, and it does not measure the
user's final monitor scanout. No claim that all G HUB UI defects are fixed follows
from this result.

SystemSage responded before compilation (87.1% used RAM); only the existing
private X11 driver was rebuilt with four build jobs. No packages were installed
or upgraded. The working disk remained around 54 GiB free and Captures around
442 GiB free. Evidence, scripts, rollback files, repaired driver, provenance and
hashes are archived at:

`/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/card-hover-presentation-20261013/`
