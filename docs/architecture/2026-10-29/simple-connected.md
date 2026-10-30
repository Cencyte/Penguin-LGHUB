# Penguin LGHUB 02 Simple Connected 2026-10-29

2026/10/29 · A compact connected map. Purple is project compatibility, turquoise is G HUB, amber is downloaded inputs and blue is the Linux host. Setup leads to the original application, private runtime and host integration.

## Graph 1: System

```mermaid
%%{init: {"theme":"default","themeVariables":{"background":"transparent","fontSize":"18px","lineColor":"#9399b2","edgeLabelBackground":"#e8e8e8"},"themeCSS":".edgeLabel rect{opacity:1}","flowchart":{"htmlLabels":false,"useMaxWidth":false,"curve":"basis","padding":12,"nodeSpacing":45,"rankSpacing":55,"wrappingWidth":300}}}%%
flowchart LR
    subgraph PREP["SETUP"]
        A["Installer"]
        B["Official inputs"]
        B --> A
    end
    subgraph RUNTIME["PRIVATE RUNTIME"]
        C["G HUB + prefix"]
        D["Wine + helpers"]
        C --> D
    end
    subgraph HOST["LINUX"]
        E["Desktop + tray"]
        F["Devices + keys"]
    end
    A --> C
    D --> E
    D --> F
    classDef readable font-size:44px,font-weight:700,font-family:Arial
    class A,D c1
    class C c2
    class B c3
    class E,F c4
    class A,B,C,D,E,F readable
    classDef c1 fill:#a78bfa,stroke:#7c3aed,color:#111,stroke-width:2px
    classDef c2 fill:#2dd4bf,stroke:#0d9488,color:#111,stroke-width:2px
    classDef c3 fill:#f59e0b,stroke:#b45309,color:#111,stroke-width:2px
    classDef c4 fill:#60a5fa,stroke:#2563eb,color:#111,stroke-width:2px
```
