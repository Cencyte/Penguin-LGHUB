# Initial GitHub source snapshot

Prepared 2026-10-26 for `Cencyte/Penguin-LGHUB`. This is a source publication,
not a packaged binary release or a claim of installation support on a new host.

The publication starts from GitHub's existing `main` commit, preserving its
GPLv3 license. It does not push the development branch's history: that history
contains captured hardware data, a vendor manual and historical build outputs.
Nothing is removed from the local development checkout.

Included material:

- All 43 numbered Wine patch topics and the clearly separated experimental
  patches, with upstream notices.
- The compatibility implementations, launch/build tools and regression
  fixtures under `tools/wine`.
- Dependency acquisition, fresh-prefix and private-runtime tooling, locks,
  sanitized verification summaries and packaging unit tests.
- Technical manuscripts and the DragonShard source tools/tests. These are
  optional contributor resources, not runtime dependencies.
- README, component notices, licenses, supplied project logo, and six native
  Mermaid diagrams with rendered previews.

Excluded material: populated prefixes, vendor application/Windows files, fonts,
installer/download caches, compiled runtime archives, raw device captures,
vendor manuals, full decompilation exports, Ghidra databases and generated
campaign work directories. Small historical DragonShard selection manifests and
validation summaries remain as research context; their local paths must be
adapted before reuse. The earlier native-remapper prototype
is outside the current Wine compatibility snapshot. No experimental database
or VM image is needed to read or build on the published implementation.

`packaging/source-publication.json` records the development revision, the export
policy and the SHA-256, size and Git mode of each published file except itself.
The public `.gitignore` comes from `packaging/source.gitignore`; the developer's
existing uncommitted ignore-file changes are left alone. Publication review
checks the selected tree for binary payloads, credential patterns and internal
documentation links. This is not a guarantee against every possible secret or
a legal clearance of separately downloaded dependencies.

Some development scripts and historical notes retain lab paths or local
evidence references. They describe the original investigation and are not
portable installation instructions. In particular, the historical
`tools/wine/installation_probe_results.json` is an investigation record, not
the current compatibility status. Use the README and the packaging verification
summaries for the current measured scope. The complete source-to-runtime build
sequence and real desktop/driver/device portability remain release work.
The pinned Linux userspace now passes the recorded native loader inventory
and isolated G HUB backend checks; see [the measured scope](linux-runtime.md).

The Obsidian deliverable is `docs/architecture/penguin-lghub-views.md`: copy the
Markdown file into a vault and use Reading view or Live Preview. Mermaid Tools
adds editing controls; Obsidian renders its six standard Mermaid fences. The
adjacent PNG is a deterministic presentation of the rendered diagrams rather
than a screenshot of the user's active Obsidian theme.

GitHub has a repository **social preview**, not a separate repository avatar.
Its documented upload is in Settings → General → Social preview. The repository
is private at this publication step; the documentation restricts first-time
social-preview uploads to public repositories. No account avatar or repository
visibility is changed by this source upload. The supplied logo remains the
README's introductory branding image and a reusable asset in the repository.


## October 29 release branch

Main was updated first at `9c8ff72` with the committed desktop/Wayland fixes and
the compact recorded-environment table. The subsequent `release` branch adds
the source-to-runtime installer, private acquisition tools, Bazzite research,
terminal presentation and persistent diagnostics. The old development branch
and its uncommitted files remain untouched.

The publication allowlist now includes the root `install.sh`. Chafa-rendered
terminal artwork is project artwork, not a vendor payload. Acquisition caches,
compiled Wine, populated prefixes, raw integration logs and official media stay
outside Git. The runtime is built locally from pinned downloads; publishing the
source does not publish Microsoft's/Logitech's binaries or a compiled Wine
release. See [the release workflow](release-installer.md).


## October 31 source preview consolidation

Preview 4 packages installer 0.3.5, including the 31 GiB offline storage budget,
verified workspace cleanup, automatic X11/Wayland session selection and clear
no-op uninstall reporting. It retains the threaded-input fix confirmed on
Bazzite. The recorded environment table now uses the October 30 booted Bazzite
report and distinguishes those confirmations from pending current-installer
acceptance on CachyOS and the Manjaro VM.

The release ZIP is created from its exact Git commit with `git archive`, without
`.git`, local caches or untracked files. `SHA256SUMS.txt` checks the release
asset's integrity against its recorded digest; it is not a substitute for trusting the
publisher. The per-file publication manifest is regenerated for the release tree.
Existing tags and their older source archives remain unchanged.

A pattern/payload review of all history reachable from the five published
branches/tags scanned 585 distinct blobs before this consolidation and found no
matches for the checked credential patterns, Windows/ELF executables, populated
prefix databases or installation-media payloads. The separate local development
history was not included. This is a scoped review, not a universal secret detector
or clearance of separately downloaded third-party components.

Repository visibility remains private at this consolidation step. Publishing a
release there does not make its assets anonymously downloadable. Visibility can
be changed separately after an explicit publication decision; it exposes both
published repository history and release assets.


## October 31 profile and Wayland consolidation

Preview 5 / installer 0.3.7 includes host-side X11/KWin automatic-profile
observers, the authenticated mailbox/agent adapter, the shared XTEST keyboard
connection and explicit profile checks in setup. Physical profile/input tests
on the original Manjaro workstation now pass. Current source build, fresh-prefix,
regression and removal results are recorded separately from remaining target
acceptance. The original and October 29 diagrams remain preserved; the README
uses four revised October 31 diagrams with their Mermaid sources.

The source-only publication keeps the existing dependency-use and binary-runtime
release boundaries. Prior release ZIPs and tags are preserved.

## November 1 authorization repair

Preview 6 / installer 0.3.8 adds an untimed pause before late sudo authentication,
confirmed-absent device-rule cleanup, and read-only inspection of Xwayland
native-input bridge support. The Manjaro failure was a password prompt timeout;
runtime and isolated UI/tray checks had already passed, and rollback removed
the incomplete installation. All 262 local regressions and 55 focused VM tests
pass. Fresh installation with this repair remains a manual acceptance step.
The source archive also contains the current README layout and sharing buttons.
Prior release archives and tags remain unchanged.

## November 1 v1.0 release

Version 1.0.0 promotes Preview 8's accepted implementation with installer 0.3.10.
Final manual uninstall/reinstall and regression acceptance is recorded for the
Manjaro VM and CachyOS on X11/Plasma Wayland, and Bazzite on Plasma Wayland.
The [release notes](release-v1.0.0.md) describe the test scope and remaining
boundaries. Runtime, installer and test code and dependency pins are unchanged
from the accepted Preview 8 revision.

The README and current environment/review records now reflect that acceptance.
Two detailed architecture diagrams have refreshed labels in a new dated folder;
the earlier source/render pairs and the compact overviews are preserved.
Historical measurements retain their original versions and dates, with a
separate current manual-acceptance record.

The versioned source ZIP is built from the tagged Git tree, with executable
script modes and a regenerated per-file manifest. Its attached checksum file
is named `SHA256SUMS`. Publication verifies the extracted archive and uploaded
asset bytes. The repository's private visibility is unchanged by this release;
anonymous access requires a separate visibility change.


## November 9 expanded-catalog testing release

Version 1.0.1-rc.1 / installer 0.4.0 adds 757 publicly extractable Logitech
resource depots, a local minimized diagnostic report, source-folder updating
and connected-device resource checks. The 22 excluded encrypted depots and 11
unreadable catalog sections are recorded explicitly. No downloaded resource,
key material or populated prefix is published. Device/model IDs, public-key
fingerprints and audited file metadata support reproducible acquisition.

The expanded prefix and isolated UI/tray tests pass; the combined suite has
286 passing tests. New manual uninstall/update/reinstall acceptance remains
pending. The original v1.0.0 release is retained. See the
[coverage record](../../packaging/device-coverage-verification.json) and
[installer instructions](release-installer.md).

`--update` uses this publication manifest to verify a source ZIP or a clean Git
checkout before replacement. The branch is resolved once to a commit, then its
complete source tree is checked against that commit's manifest. This protects
against corrupt/incomplete downloads and accidental local modifications; the
manifest is not an independent digital signature. Repository/TLS trust remains
required. Updating never installs the payload or changes an existing prefix.


## November 10 follow-up testing release

Version 1.0.1-rc.2 / installer 0.4.1 changes diagnostic delivery to a single `.tar.gz` containing the text report and adds bounded resume/retry handling for interrupted downloads. Both reported installer failures occurred while fetching `core.depot`; a fresh official transfer reproduced the interruption and passed full size/hash verification after automatic resumption. The 289 project tests pass. Device recognition beyond the tested hardware still requires reporter diagnostics; no new physical-device support is claimed. Exact excluded-depot identities and known metadata associations are recorded in `packaging/excluded-depot-identities.json`.
