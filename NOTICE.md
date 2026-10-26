# Component notices

Original Penguin-LGHUB implementation source is provided under the repository's
GPLv3 license, except where a file or component specifies another license.
Copyright and license notices already present in files remain in effect.

- **Wine patches and Wine-derived material:** LGPL-2.1-or-later, with the Wine
  authors' and other upstream contributors' notices retained. See
  [the Wine license](packaging/licenses/Wine-LGPL-2.1.txt). The numbered patches
  include upstream backports as well as project changes; they are not all
  original Penguin-LGHUB inventions. The base source is Wine 10.20. This source
  snapshot does not distribute the assembled Wine binary runtime. Its complete
  source/build-reproduction audit remains a prerequisite for that release.
- **Windows acquisition helper:** files explicitly marked GPL-3.0-or-later retain
  that license. The separately fetched `ms-compress` source is Copyright (C) 2012
  Jeffrey Bush, GPL-3.0-or-later; its archive and hash are pinned in
  `packaging/windows-acquisition.lock.json`. No decoder binary is included.
- **Dockur:** the acquisition experiment references the MIT-licensed Dockur
  project. Its notice is retained in `packaging/dockur/Dockur-LICENSE.txt`.
  Neither that notice nor this project's license grants rights to Windows.
- **Optional Linux runtime experiment:** the tools download Valve's pinned
  Steam Linux Runtime and Debian's pinned libunwind package. These contain
  independently licensed components, not project-GPL artifacts. The extractor
  preserves their supplied notices and records upstream source locations in
  `packaging/linux-runtime.lock.json`. No runtime/package binaries are in this
  source snapshot. Any future binary release must satisfy each component's
  corresponding-source and notice requirements.
- **Proprietary inputs:** Logitech applications/resources and Microsoft Windows
  DLLs/fonts retain their own terms. Download metadata, hashes, acquisition
  recipes and local transformation code do not relicense those inputs. Their
  binaries and generated resource archives are absent from this snapshot.
- **Project artwork:** `assets/penguin-lghub-logo.png` is the owner-supplied
  project identity image, with the circle-edge cleanup requested by the owner. It is not
  covered by the source-code licensing statement above; no new permission for
  separately sourced elements is asserted. Tux, where derived from Larry
  Ewing's original, requires credit to Larry Ewing and GIMP. This notice does
  not establish the provenance of every element of the supplied composite.
- **Diagrams and documentation:** project-authored material is included with
  the source. Third-party names, quoted identifiers and linked documents
  retain their respective ownership; linking does not relicense them.

The project is independent and is not endorsed by Logitech, Microsoft, Wine,
Arch Linux, Manjaro or CachyOS. For dependency-use research and remaining
questions, see [the third-party note](docs/packaging/third-party-use.md).
