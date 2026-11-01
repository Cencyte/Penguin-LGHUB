# Penguin LGHUB 03 Detailed Modular 2026-10-31

2026/10/31 · Six updated views of acquisition, source builds, launch, compatibility, Linux integration and evidence. Purple is project implementation; turquoise is vendor application code. Installation is confirmed on all three target distributions; native profiles have Manjaro acceptance.

## Graph 1: 1 · System overview

```mermaid
%%{init: {"theme":"default","themeVariables":{"background":"transparent","fontSize":"18px","lineColor":"#9399b2","edgeLabelBackground":"#e8e8e8"},"themeCSS":".edgeLabel rect{opacity:1}","flowchart":{"htmlLabels":false,"useMaxWidth":false,"curve":"basis","padding":12,"nodeSpacing":45,"rankSpacing":55,"wrappingWidth":300}}}%%
flowchart LR
    A["Desktop launcher and icon"] --> B["Dedicated prefix · G HUB 2026.6.974819"]
    B --> C["Original G HUB · interface, agent and tray"]
    C --> D["Private Wine 10.20 · patches and helper DLLs"]
    D --> E["Pinned Linux userspace · pressure-vessel and bubblewrap"]
    E --> F["Host graphics, desktop and tray · X11 or Plasma Wayland via Xwayland"]
    E --> G["USB / HID access · Logitech peripherals"]
    E --> H["Linux key output · one shared XTEST connection · libei / KWin consent on Wayland"]
    F --> I["Host observer · X11 focus or temporary KWin script"]
    I --> J["Authenticated mailbox → profile adapter → G HUB automatic profiles"]
    class I,J c1
    class A,B,D,E c1
    class C c2
    classDef c1 fill:#a78bfa,stroke:#7c3aed,color:#111,stroke-width:2px
    classDef c2 fill:#2dd4bf,stroke:#0d9488,color:#111,stroke-width:2px
```

## Graph 2: 2 · Complete installer pipeline

```mermaid
%%{init: {"theme":"default","themeVariables":{"background":"transparent","fontSize":"18px","lineColor":"#9399b2","edgeLabelBackground":"#e8e8e8"},"themeCSS":".edgeLabel rect{opacity:1}","flowchart":{"htmlLabels":false,"useMaxWidth":false,"curve":"basis","padding":12,"nodeSpacing":45,"rankSpacing":55,"wrappingWidth":300}}}%%
flowchart LR
    A["install.sh · logo, colored steps and persistent logs"] --> B["Host preflight · session, namespaces, graphics, devices and policy"]
    B --> C["Private SDK and Wine 10.20 source · verified downloads"]
    C --> D["Apply 46 patch topics · build every replacement and helper"]
    E["Pinned Arch Wine base + Linux runtime + Node / test helpers"] --> F["Complete private runtimes · no host Wine or MinGW"]
    D --> F
    G["Official Windows media and updates"] --> H["Bubblewrap or rootless Podman reconstruction · 2 exact DLL hashes"]
    I["Official pinned Logitech depots · 30 archives"] --> J["Verify and extract · 1,115 depot files · 438 application files"]
    F --> K["Fresh prefix · registry, services, DLL overrides, depot state and resource fixes"]
    H --> K
    J --> K
    K --> L["Isolated loader, activation, UI and tray tests · offscreen host graphics/audio probe"]
    L --> P["Verify both profile backends and pinned agent adapter"] --> M["Scoped sudo device rule · one automatic Desktop and application-menu launcher"]
    M --> N["Commit installation · rollback on failure · receipt-scoped uninstall"]
    class P,N c1
    class A,B,C,D,F,H,J,K,L,M c1
    class G,I c2
    classDef c1 fill:#a78bfa,stroke:#7c3aed,color:#111,stroke-width:2px
    classDef c2 fill:#2dd4bf,stroke:#0d9488,color:#111,stroke-width:2px
```

## Graph 3: 3 · Desktop launch and application

```mermaid
%%{init: {"theme":"default","themeVariables":{"background":"transparent","fontSize":"18px","lineColor":"#9399b2","edgeLabelBackground":"#e8e8e8"},"themeCSS":".edgeLabel rect{opacity:1}","flowchart":{"htmlLabels":false,"useMaxWidth":false,"curve":"basis","padding":12,"nodeSpacing":45,"rankSpacing":55,"wrappingWidth":300}}}%%
flowchart LR
    A["Desktop entry"] --> B["desktop_runtime.py · integrity, session checks, ownership and readiness"]
    B --> C["pressure-vessel · pinned userspace with host graphics and session sockets"]
    C --> D["Private prefix · registrations, saved settings and DLL policy"]
    D --> E["lghub_agent.exe · devices, profiles, assignments, macros, G-Shift and lighting"]
    D --> F["lghub.exe · Electron interface"]
    D --> G["lghub_system_tray.exe · original WinUI menu"]
    F <-- "local API" --> E
    G <-- "agent integration" --> E
    B --> H["Update freeze · checks and downloads off · software manager blocked · application and depots read-only"]
    H --> I["lghub_updater.exe · service and lifecycle retained"]
    B --> J["Per-app NTSYNC masking · Wine server synchronization"]
    J --> C
    B -. "read-only readiness" .-> K["Bundled Node · agent snapshots"]
    K -. "local WebSocket" .-> E
    class A,B,C,D,H,J,K c1
    class E,F,G,I c2
    classDef c1 fill:#a78bfa,stroke:#7c3aed,color:#111,stroke-width:2px
    classDef c2 fill:#2dd4bf,stroke:#0d9488,color:#111,stroke-width:2px
```

## Graph 4: 4 · Supplementary components and resources

```mermaid
%%{init: {"theme":"default","themeVariables":{"background":"transparent","fontSize":"18px","lineColor":"#9399b2","edgeLabelBackground":"#e8e8e8"},"themeCSS":".edgeLabel rect{opacity:1}","flowchart":{"htmlLabels":false,"useMaxWidth":false,"curve":"basis","padding":12,"nodeSpacing":45,"rankSpacing":55,"wrappingWidth":300}}}%%
flowchart LR
    A["G HUB agent"] --> B["ApiInformation proxy · CoreApplication ABI · sensor fallback + native profile-event adapter"]
    C["Original WinUI tray"] --> D["DispatcherQueue adapter · popup anchoring"]
    C --> E["CoreMessaging adapter · guarded XP / FrameworkUdk adaptation · Wine fallback"]
    C --> F["DirectComposition implementation · visuals, surfaces and input binding"]
    C --> G["Vendor stack · Xaml, Controls, Phone, UI.Input, InputStateManager and DirectManipulation"]
    G --> H["Vendor composition · dcompi, dwmcorei, marshal and D3DCompiler_47"]
    G --> I["Windows d2d1.dll and ninput.dll · reconstructed locally from official media"]
    J["Local resource transformations"] --> K["Electron app.asar · color picker, startup and graceful shutdown"]
    J --> L["MainWindow.xbf and resources.pri · tray background"]
    C --> M["Font configuration · bundled BrownPro and private-runtime DejaVu Sans"]
    class B,D,E,F,J,K,L,M c1
    class A,C,G,H c2
    classDef c1 fill:#a78bfa,stroke:#7c3aed,color:#111,stroke-width:2px
    classDef c2 fill:#2dd4bf,stroke:#0d9488,color:#111,stroke-width:2px
```

## Graph 5: 5 · Wine changes and Linux integration

```mermaid
%%{init: {"theme":"default","themeVariables":{"background":"transparent","fontSize":"18px","lineColor":"#9399b2","edgeLabelBackground":"#e8e8e8"},"themeCSS":".edgeLabel rect{opacity:1}","flowchart":{"htmlLabels":false,"useMaxWidth":false,"curve":"basis","padding":12,"nodeSpacing":45,"rankSpacing":55,"wrappingWidth":300}}}%%
flowchart LR
    A["Source-built Wine 10.20"] --> B["ntdll / wineserver · waits, ALPC and composition protocol"]
    A --> C["win32u / user32 / winex11 / dwmapi · pointer input, tray, restore and startup"]
    A --> D["d3d11 / dxgi / wined3d · shared resources and rendering"]
    A --> E["dwrite / windows.ui / storage / threadpoolwinrt · fonts and activation"]
    A --> F["kernelbase / setupapi / ntoskrnl / shcore · filesystem and device contracts"]
    C --> G["X11 or Plasma Wayland via Xwayland · compositor and tray proxy"]
    C --> H["Native application key delivery · XTEST / libei and KWin authorization"]
    D --> I["Imported host graphics driver · offscreen probe and manual GPU qualification"]
    F --> J["Linux USB and HID · scoped udev permissions and logind ACLs"]
    A --> K["Pinned Linux libraries + host audio, D-Bus, filesystem and sockets"]
    class A,B,C,D,E,F,H c1
    classDef c1 fill:#a78bfa,stroke:#7c3aed,color:#111,stroke-width:2px
    classDef c2 fill:#2dd4bf,stroke:#0d9488,color:#111,stroke-width:2px
```

## Graph 6: 6 · Validation and release boundary

```mermaid
%%{init: {"theme":"default","themeVariables":{"background":"transparent","fontSize":"18px","lineColor":"#9399b2","edgeLabelBackground":"#e8e8e8"},"themeCSS":".edgeLabel rect{opacity:1}","flowchart":{"htmlLabels":false,"useMaxWidth":false,"curve":"basis","padding":12,"nodeSpacing":45,"rankSpacing":55,"wrappingWidth":300}}}%%
flowchart LR
    A["Ghidra / DragonShard · development-only evidence"] --> B["Implementation and regression fixtures"]
    B --> C["Installer, removal, input and native-profile regression tests"]
    B --> D["Source build, fresh-prefix activation and isolated UI/tray checks"]
    D --> E["46 native modules + 53 dynamic libraries + real Windows command"]
    E --> F["8 activation factories · 4 registry routes · 6 first-run UI / tray checks"]
    F --> G["Manjaro and CachyOS · X11 / Wayland desktop testing"]
    G --> H["Installations confirmed on all three OSes · Manjaro native X11 and Wayland profiles confirmed"]
    I["GitHub source · no proprietary payloads, populated prefixes or analysis databases"] --> B
    H --> J["Remaining · new packaged-profile acceptance on CachyOS/Bazzite and component-use policy"]
    class A,B,C,D,E,F,G,H,I,J c1
    classDef c1 fill:#a78bfa,stroke:#7c3aed,color:#111,stroke-width:2px
    classDef c2 fill:#2dd4bf,stroke:#0d9488,color:#111,stroke-width:2px
```
