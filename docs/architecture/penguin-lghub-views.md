# Penguin LGHUB System

2026/10/26 · Six views of the existing Desktop setup and the fresh-install development pipeline. Proprietary dependencies are acquired locally; cross-machine GUI and device qualification remains pending.

## Graph 1: 1 · System overview

```mermaid
%%{init: {"theme":"default","themeVariables":{"background":"transparent","fontSize":"18px","lineColor":"#9399b2","edgeLabelBackground":"#e8e8e8"},"themeCSS":".edgeLabel rect{opacity:1}","flowchart":{"htmlLabels":false,"useMaxWidth":false,"curve":"basis","padding":12,"nodeSpacing":45,"rankSpacing":55,"wrappingWidth":300}}}%%
flowchart LR
    A["Penguin-LGHUB launcher"] --> B["Dedicated Wine prefix · G HUB 2026.6.974819"]
    B --> C["Original G HUB · interface, agent and tray"]
    C --> D["Compatibility DLLs and patched Wine 10.20"]
    D --> E["Linux X11 desktop · graphics and tray"]
    D --> F["USB and HID · Logitech peripherals"]
    D --> G["XTEST key output · native Linux applications"]
    class A,B,D c1
    class C c2
    classDef c1 fill:#a78bfa,stroke:#7c3aed,color:#111,stroke-width:2px
    classDef c2 fill:#2dd4bf,stroke:#0d9488,color:#111,stroke-width:2px
```

## Graph 2: 2 · Reproducible preparation

```mermaid
%%{init: {"theme":"default","themeVariables":{"background":"transparent","fontSize":"18px","lineColor":"#9399b2","edgeLabelBackground":"#e8e8e8"},"themeCSS":".edgeLabel rect{opacity:1}","flowchart":{"htmlLabels":false,"useMaxWidth":false,"curve":"basis","padding":12,"nodeSpacing":45,"rankSpacing":55,"wrappingWidth":300}}}%%
flowchart LR
    A["Official pinned Logitech depots · 30 archives"] --> B["Verify and extract · 1,115 files, 438 application files"]
    C["Official Windows media and updates"] --> D["Reconstruct matching d2d1.dll and ninput.dll · hashes verified"]
    E["Signed Arch Wine 10.20-2 package"] --> F["Private runtime packager · 20 Wine replacements and 7 compatibility DLLs"]
    H["Project source and Wine patches"] --> I["Build helpers · compiled inputs still required"] --> F
    B --> J["Clean-prefix preparation · new registry, services, overrides and local asset patches"]
    D --> J
    F --> J
    J --> K["Isolated backend validation passed · fresh UI and real devices pending"]
    D -. "acquisition does not settle use permissions" .-> L["Dependency licensing and font qualification remain open"]
    class B,D,F,H,I,J,K c1
    class A,C c2
    classDef c1 fill:#a78bfa,stroke:#7c3aed,color:#111,stroke-width:2px
    classDef c2 fill:#2dd4bf,stroke:#0d9488,color:#111,stroke-width:2px
```

## Graph 3: 3 · Desktop launch and application

```mermaid
%%{init: {"theme":"default","themeVariables":{"background":"transparent","fontSize":"18px","lineColor":"#9399b2","edgeLabelBackground":"#e8e8e8"},"themeCSS":".edgeLabel rect{opacity:1}","flowchart":{"htmlLabels":false,"useMaxWidth":false,"curve":"basis","padding":12,"nodeSpacing":45,"rankSpacing":55,"wrappingWidth":300}}}%%
flowchart LR
    A["Desktop entry"] --> B["tested_desktop_launch.py · ownership, readiness and pinned files"]
    B --> C["Existing Desktop: private mount overlays, then user UID"]
    C --> D["Dedicated prefix · registry, services, DLL overrides and saved settings"]
    D --> E["lghub_agent.exe · devices, profiles, bindings, macros, G-Shift and lighting"]
    D --> F["lghub.exe · Electron interface"]
    D --> G["lghub_system_tray.exe · original menu"]
    F <-- "local API" --> E
    G <-- "agent integration" --> E
    B --> H["G HUB update freeze · periodic checks and downloads off; software manager blocked"]
    H --> I["lghub_updater.exe · retained for service and lifecycle"]
    B -. "read-only checks" .-> J["Agent snapshots and optional UI diagnostics"]
    J -. "local WebSocket" .-> E
    class A,B,C,D,H,J c1
    class E,F,G,I c2
    classDef c1 fill:#a78bfa,stroke:#7c3aed,color:#111,stroke-width:2px
    classDef c2 fill:#2dd4bf,stroke:#0d9488,color:#111,stroke-width:2px
```

## Graph 4: 4 · Supplementary components and resources

```mermaid
%%{init: {"theme":"default","themeVariables":{"background":"transparent","fontSize":"18px","lineColor":"#9399b2","edgeLabelBackground":"#e8e8e8"},"themeCSS":".edgeLabel rect{opacity:1}","flowchart":{"htmlLabels":false,"useMaxWidth":false,"curve":"basis","padding":12,"nodeSpacing":45,"rankSpacing":55,"wrappingWidth":300}}}%%
flowchart LR
    A["G HUB agent"] --> B["ApiInformation proxy · CoreApplication adapter · sensor-unavailable response"]
    C["Original WinUI tray"] --> D["DispatcherQueue adapter · popup anchoring"]
    C --> E["CoreMessaging adapter · guarded XP and FrameworkUdk adaptation · Wine fallback"]
    C --> F["DirectComposition implementation · visuals, surfaces and input binding"]
    C --> G["Vendor stack · Xaml, Controls, Phone, UI.Input, InputStateManager, DirectManipulation"]
    G --> H["Vendor composition · dcompi, dwmcorei, marshal and D3DCompiler_47"]
    G --> I["Windows d2d1.dll and ninput.dll · acquired separately"]
    J["Local resource patch tools"] --> K["Electron app.asar · color-picker responsiveness"]
    J --> L["Tray MainWindow.xbf and resources.pri · background correction"]
    C --> M["Fonts · existing Windows set; clean-install substitutes unqualified"]
    class B,D,E,F,J,K,L c1
    class A,C,G,H c2
    classDef c1 fill:#a78bfa,stroke:#7c3aed,color:#111,stroke-width:2px
    classDef c2 fill:#2dd4bf,stroke:#0d9488,color:#111,stroke-width:2px
```

## Graph 5: 5 · Wine changes and host integration

```mermaid
%%{init: {"theme":"default","themeVariables":{"background":"transparent","fontSize":"18px","lineColor":"#9399b2","edgeLabelBackground":"#e8e8e8"},"themeCSS":".edgeLabel rect{opacity:1}","flowchart":{"htmlLabels":false,"useMaxWidth":false,"curve":"basis","padding":12,"nodeSpacing":45,"rankSpacing":55,"wrappingWidth":300}}}%%
flowchart LR
    A["Patched Wine 10.20"] --> B["ntdll and wineserver · waits, ALPC and matched composition protocol"]
    A --> C["win32u, user32, winex11 and dwmapi · input routing, pointer behavior, tray and presentation"]
    A --> D["d3d11, dxgi and wined3d · shared resources, surfaces and rendering"]
    A --> E["dwrite, windows.ui, windows.storage and threadpoolwinrt · text and WinRT contracts"]
    A --> F["kernelbase, setupapi, ntoskrnl and shcore · files, devices and feature calls"]
    C --> G["X11 and Plasma · window manager, compositor and panel"]
    C --> H["Opt-in XTEST bridge · Linux key delivery"]
    D --> I["Host graphics driver and OpenGL · still external to the bundle"]
    F --> J["Linux USB and HID nodes · scoped permissions and hotplug"]
    A --> K["Native Linux libraries, audio, user session, filesystem and sockets"]
    class A,B,C,D,E,F,H c1
    classDef c1 fill:#a78bfa,stroke:#7c3aed,color:#111,stroke-width:2px
    classDef c2 fill:#2dd4bf,stroke:#0d9488,color:#111,stroke-width:2px
```

## Graph 6: 6 · Evidence and remaining release work

```mermaid
%%{init: {"theme":"default","themeVariables":{"background":"transparent","fontSize":"18px","lineColor":"#9399b2","edgeLabelBackground":"#e8e8e8"},"themeCSS":".edgeLabel rect{opacity:1}","flowchart":{"htmlLabels":false,"useMaxWidth":false,"curve":"basis","padding":12,"nodeSpacing":45,"rankSpacing":55,"wrappingWidth":300}}}%%
flowchart LR
    A["DragonShard and Ghidra · development-only analysis"] --> B["Implementation, regression fixtures and saved findings"]
    B --> C["Existing Desktop · six feature groups accepted by owner on Manjaro X11"]
    B --> D["Fresh prefix · 8 factories, 4 registry routes, 1 service and 5 API routes passed"]
    D --> E["Packaged runtime · relocation and archive restore passed"]
    E --> F["Next: rebuild recipe, Linux-library portability and fonts"]
    F --> G["Fresh Manjaro control · original UI, tray, devices and input"]
    G --> H["CachyOS · driver, session, permissions and hotplug differences"]
    H --> I["Later: Debian qualification"]
    J["GitHub source · no proprietary DLLs, populated prefixes or analysis corpus"] -. "supports review and reproduction work" .-> B
    class A,B,D,E,F,G,H,I,J c1
    classDef c1 fill:#a78bfa,stroke:#7c3aed,color:#111,stroke-width:2px
    classDef c2 fill:#2dd4bf,stroke:#0d9488,color:#111,stroke-width:2px
```
