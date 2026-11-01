# Penguin LGHUB 01 Simple Modular 2026-10-31

2026/10/31 · Three compact views of setup, runtime and Linux integration. Purple is project compatibility code; turquoise is the original G HUB application. Detailed component maps follow in the repository.

## Graph 1: 1 · Install

```mermaid
%%{init: {"theme":"default","themeVariables":{"background":"transparent","fontSize":"18px","lineColor":"#9399b2","edgeLabelBackground":"#e8e8e8"},"themeCSS":".edgeLabel rect{opacity:1}","flowchart":{"htmlLabels":false,"useMaxWidth":false,"curve":"basis","padding":12,"nodeSpacing":45,"rankSpacing":55,"wrappingWidth":300}}}%%
flowchart LR
    A["Verified inputs"] --> B["Build + test"] --> C["Ready to launch"]
    classDef readable font-size:44px,font-weight:700,font-family:Arial
    class A,B,C c1
    class A,B,C readable
    classDef c1 fill:#a78bfa,stroke:#7c3aed,color:#111,stroke-width:2px
    classDef c2 fill:#2dd4bf,stroke:#0d9488,color:#111,stroke-width:2px
```

## Graph 2: 2 · Run

```mermaid
%%{init: {"theme":"default","themeVariables":{"background":"transparent","fontSize":"18px","lineColor":"#9399b2","edgeLabelBackground":"#e8e8e8"},"themeCSS":".edgeLabel rect{opacity:1}","flowchart":{"htmlLabels":false,"useMaxWidth":false,"curve":"basis","padding":12,"nodeSpacing":45,"rankSpacing":55,"wrappingWidth":300}}}%%
flowchart LR
    A["Original G HUB"] --> B["Private Wine"] --> C["Linux libraries"]
    classDef readable font-size:44px,font-weight:700,font-family:Arial
    class A c2
    class B,C c1
    class A,B,C readable
    classDef c1 fill:#a78bfa,stroke:#7c3aed,color:#111,stroke-width:2px
    classDef c2 fill:#2dd4bf,stroke:#0d9488,color:#111,stroke-width:2px
```

## Graph 3: 3 · Connect

```mermaid
%%{init: {"theme":"default","themeVariables":{"background":"transparent","fontSize":"18px","lineColor":"#9399b2","edgeLabelBackground":"#e8e8e8"},"themeCSS":".edgeLabel rect{opacity:1}","flowchart":{"htmlLabels":false,"useMaxWidth":false,"curve":"basis","padding":12,"nodeSpacing":45,"rankSpacing":55,"wrappingWidth":300}}}%%
flowchart LR
    A["Linux host"] --> B["X11 / Wayland + tray"]
    A --> C["Devices + keys"]
    A --> D["Automatic profiles"]
    class A c1
    classDef readable font-size:44px,font-weight:700,font-family:Arial
    class A,B,C,D readable
    classDef c1 fill:#a78bfa,stroke:#7c3aed,color:#111,stroke-width:2px
    classDef c2 fill:#2dd4bf,stroke:#0d9488,color:#111,stroke-width:2px
```
