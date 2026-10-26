# Third-party software and acquisition notice

Draft for review, 2026-10-26. This explains the experiment and its unresolved publication requirements. It is **not** a new software license, a Microsoft permission grant, or a legal opinion. It does not add restrictions to rights granted by the project's or its dependencies' open-source licenses.

## What the project supplies

Penguin-LGHUB supplies compatibility implementation, patches, configuration and acquisition tooling. Microsoft Windows, Windows fonts and Logitech G HUB remain their respective owners' software. Downloading those products does not transfer their ownership or make them open source.

The acquisition paths under investigation get selected media from Microsoft directly and stage selected files locally, either by direct image extraction or by installing a donor VM first. The repository does not supply Windows media, populated Windows disks, extracted Microsoft DLLs/fonts, product keys, or the developer's populated Wine prefix. Running an installer locally avoids this project's hosting those binaries; it does not establish a right to execute extracted components under Wine.

A participant needs an appropriate license for the Windows guest and must follow the terms applicable to that edition, acquisition channel and use. A Windows license already assigned to another machine or VM is not automatically an additional VM license. Evaluation media have separate eligibility, registration and duration restrictions. An evaluation is not a perpetual production dependency source. Successful activation, a digital signature or a matching hash is not a determination of legal entitlement.

## Dockur's actual license

[Dockur 6.06 source](https://github.com/dockur/windows/tree/013ea27498e0e820a46b3861a0fa047d5b19d846) is MIT licensed. Its unmodified notice is retained at [Dockur-LICENSE.txt](../../packaging/dockur/Dockur-LICENSE.txt). Credit belongs to the Dockur contributors. This integration uses Dockur's documented container, custom-ISO and OEM-script interfaces; the orchestration and collector here are project additions.

The MIT terms permit reuse and modification of covered code/documentation with the required notices. They do not license Windows. Dockur's README explanation about users' Windows licensing responsibilities is explanatory material, not a second Windows license or an exemption. We can retain that useful distinction without copying an assurance that our materially different DLL-extraction use has been approved. Wine code retains its LGPL obligations; the project's existing GPL-3.0 licensing choice does not replace third-party licenses. Do not relabel the combined system MIT.

## The unresolved component-use question

The reviewed [Windows terms](https://www.microsoft.com/content/dam/microsoft/usetm/documents/windows/11/oem-%28pre-installed%29/Useterms_OEM_Windows_11_EnglishGreatBritain.pdf) distinguish licensed physical/virtual devices (§2(b), §2(d)(iv)) from separate-feature use and copying restrictions (§2(c)). They expressly allow a licensed virtual instance; Linux as the host is not prohibited there. The actual selected media's terms still control, particularly for enterprise/evaluation editions.

“File,” “component” and “feature” describe different aspects of software, not mutually exclusive legal classes. Calling a DLL a component does not establish that its implemented functionality falls outside a separate-use restriction. G HUB calling Microsoft's drawing/input code also does not make that implementation Logitech's. The copying clause is an additional issue independent of the word “feature.” This is an interpretation to obtain advice on, not a court finding about these two DLLs. Applicable statutory interoperability rights may also matter; this experiment has not established their application to this distribution model.

The project has no identified written permission covering the entire proposed DLL/font set. A project notice cannot create that permission, disclaim away another owner's rights, or determine enforceability across jurisdictions. No warranty of compatibility or legal clearance is made by the acquisition test. Applicable mandatory rights and the licenses governing each component remain unaffected.

## Concrete permission route

Microsoft's [technology-licensing page](https://www.microsoft.com/en-us/legal/intellectualproperty/tech-licensing) links to a public [licensing inquiry form](https://iplicensingpublic.powerappsportals.com/page/IPlicensing/). It has an **Other** program selection with a description field. It asks for legal company identity, title, address, website and company email, so it is oriented toward organizations; no invite or outside connection is shown as a prerequisite. The page does not establish that an unincorporated individual qualifies, or promise acceptance, price or turnaround. A solo maintainer can seek routing/eligibility information without inventing a company.

This is a concrete inquiry route, **not** a self-service license for arbitrary Windows DLLs. Microsoft's [licensing policy](https://www.microsoft.com/en-us/legal/intellectualproperty/tech-licensing/policy) does not commit it to granting every request. Patent/protocol licensing is distinct from permission to copy and execute OS binary implementations.

The factual scope to resolve is:

- Exact `d2d1.dll` and `ninput.dll` versions/hashes, plus the required font set.
- Local automated extraction from legitimately licensed Microsoft media and execution under Wine on Linux.
- Whether every user must hold a particular Windows license, and whether rights survive removal/expiration of the donor VM.
- Whether direct Microsoft acquisition is sufficient or a separate grant is necessary; alternatively, what redistribution/sublicensing rights would be available.
- A free, community-maintained project with a public installer; conditions, notices, costs and version coverage.

No inquiry has been submitted. This list is technical scope for the maintainer's own inquiry, not a message written on his behalf.

## What Wine's history actually establishes

[Wine's own history](https://www.winehq.org/about/) dates its start to 1993 under Bob Amstadt, with leadership passing to Alexandre Julliard and version 1.0 arriving in 2008. Its central approach is a compatibility implementation of Windows interfaces, rather than distributing a Windows installation.

Legal concerns were real. In [February 2005](https://www.winehq.org/pipermail/wine-devel/2005-February/033437.html), CodeWeavers' Jeremy White discussed seeking copyright and patent advice. On [11 May 2005, SFLC announced representation of Wine](https://softwarefreedom.org/news/2005/may/11/wine/). That announcement describes legal counsel, not a Microsoft settlement or blanket Windows-binary redistribution license. The sources reviewed did not identify a court case in which Wine won such permission; this is not proof that no dispute ever occurred.

The practical lesson is to distinguish our implementation rights from the licenses governing native Microsoft files. CodeWeavers made that distinction for [native GDI+ redistribution](https://www.codeweavers.com/compatibility/crossover/forum/platform-sdk-redistributable-gdi?msg=113851). Wine's longevity is not a license for our proposed extraction path.

There is also a concrete avenue for advice: [SFLC's services](https://softwarefreedom.org/services/) include FOSS licensing assistance and [its contact page](https://softwarefreedom.org/about/contact/) provides an inquiry route. Acceptance or representation of this project is not established. Seeking that focused assessment alongside Microsoft's inquiry is more informative than treating ambiguous terminology as clearance.
