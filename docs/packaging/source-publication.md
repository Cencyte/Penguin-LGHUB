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
