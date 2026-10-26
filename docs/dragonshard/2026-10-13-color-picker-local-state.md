# Chapter 17 — Measure the color dot, not just incoming mouse events

Cencyte clarified that the saturation dot moved only about once every ten
seconds. That observation changed the investigation: the first recorder measured
mouse-event age and animation-frame scheduling, not selected-color state or dot
position. Its roughly 3 ms event age and 60 Hz frame callbacks did not demonstrate
a responsive picker. The recorded 69–82 ms long tasks were real, but could not
by themselves explain a dot that stayed put for seconds.

## The state overwrite and the delayed parent

Read-only inspection of the already running frontend identified the actual
`.saturation-white` element and its React ancestors. The saturation component's
50 ms throttle feeds an inline ColorWrap instance. Its `handleChange` computes
the new HSV/color state and calls `setState`, but `getDerivedStateFromProps`
unconditionally replaces that state with a conversion of `props.color`.

The enclosing G HUB picker supplies `onChangeComplete`, debounced for 100 ms;
it does not supply an immediate parent-color update. That completed callback
updates the enclosing picker's color and calls `updateColor` for the existing
profile/device path. During continuous movement, the parent color therefore
stays old, and ColorWrap overwrites its own local preview with that old value.
A sufficiently long pause lets the completion callback publish a new parent
color. This is a concrete mechanism for a frozen dot despite prompt mouse input.
It does not imply a fixed ten-second timer.

The original isolated benchmark had mounted Saturation directly with an
immediate React state setter. It omitted ColorWrap and the delayed parent, so
its 19-versus-60 callback-rate result did not test the reported failure. The
expanded fixture uses the installed bundle's ColorWrap and Saturation, and
models the enclosing parent's existing completion callback. The shipped bundle
contains a module and an inline copy of ColorWrap; runtime inspection identified
the inline copy as the application path. The fixture tests the equivalent module
copy. Both have the same unconditional derived-state behavior.

## Fix and measured comparison

The patch opts in only this G HUB picker with `ghubLocalPreview`. That instance
remembers its last parent color and retains local drag state while the incoming
parent color is unchanged. A different parent color still replaces local state;
unflagged ColorWrap users retain the original behavior. The saturation throttle,
completion debounce and device/profile callback are unchanged. No per-mouse-event
device writes were added.

| Owned Xvfb comparison | Original controlled picker | Local-state fix |
| --- | --- | --- |
| Three-second drag: dot updates while held (DOM mutations) | 0 | 57 |
| Twelve-second drag: dot updates while held (DOM mutations) | 0 | 230 |
| Twelve-second drag: dot position changes sampled on frames | 0 | 229 |
| Twelve-second drag: median interval between dot changes | none | 50 ms |
| Twelve-second drag: longest stale position while pointer moves | 11,999.6 ms | 50.1 ms |
| Delayed completion callbacks in each comparison | 1 | 1 |
| Completion callbacks while continuously dragging | 0 | 0 |
| Final committed color, three-second trial | `#ad7886` | `#ad7886` |
| Final committed color, twelve-second trial | `#22040b` | `#22040b` |

The revised passive recorder measures the actual saturation/hue dot's percentage
position, position-change intervals, stale duration while the pointer continues
moving and pointer-to-dot distance. Sampling on frames can coalesce mutations,
so its count can be slightly below the fixture's MutationObserver count. It
still does not measure final display scanout or physical-device lighting.

The fixed fixture also passed three consecutive drags: **60, 60 and 56** DOM
position updates while held, with one deferred completion each. Three state
checks pass: unchanged parent retains local state, changed external color is
applied, and unflagged instances preserve their prior behavior.

Early attempts are retained: a fixture imported `flushSync` from the client-only
ReactDOM export, then was corrected to the installed ReactDOM module; repeated
baseline runs delivered only two recorded drags and failed the third-input
requirement. Fresh-process baseline comparisons produced the completed evidence
above. The exact reason for the baseline third-input loss remains unassigned;
it is not counted as a pass. Tests now reject renderer/JavaScript errors, verify
deferred callback counts, and independently check the passive dot measurements.

## A separate archive, privately mounted

`prepare_color_picker_fix.py` accepts only the known bundle hash and checks that
each of its two patch sites occurs exactly once. It writes a new ASAR rather than
changing the installation, updates packed offsets and entry/block integrity,
and verifies **267 packed entries**, with only `app.min.js` changed. Tests reject
an unknown bundle, an unpinned frontend and a frontend without the update freeze.

The launcher pins the separate archive and passes it to `run_private_build.py`.
After mounting the application directory read-only, the private namespace
mounts the replacement archive read-only over `resources/app.asar`. The host and
any already running session retain their original file. Inside the new session,
the launcher validates the mounted archive against the replacement hash; outside
it, the installed original and replacement both retain their own checks.

An isolated full G HUB session passes **27/27 checks**: the patched frontend
mount is present and read-only, the host archive is unchanged during and after
the session, the real frontend opens and reopens, recording attaches again,
the updater restrictions persist, and shutdown is clean. Hardware drivers are
disabled in this test prefix, and the network namespace has only loopback.
This establishes application loading and launcher compatibility, not physical
device acceptance or proof of the user's final screen presentation.

## Handoff and remaining work

Implementation is complete for this reproduced local-state defect. Measured
coverage includes original/fixed short and sustained drags, repeated fixed
drags, three state-transition checks, four integrity/config guards and the
27-check native launch run. User-desktop acceptance of the dot fix is pending.
The previously recorded renderer stalls and physical-device latency may still
need separate work after this visible freeze is removed.

The Desktop installation receives the new archive and runner/recorder scripts
without stopping or manipulating Cencyte's live window. **Quit this G HUB session**
from the Desktop launcher's context menu, then reopen it to activate the new
private mount. The performance-recording action now records the dot itself.
Rollback is the saved launch manifest and runner scripts; the original vendor
archive was never overwritten. No synthetic input was sent to the user's display.

Evidence, provenance and rollback copies:
`/mnt/Captures/DragonShard-2/Penguin-GHUB/campaign-20261006-214743/color-picker-local-state-20261013/`.
