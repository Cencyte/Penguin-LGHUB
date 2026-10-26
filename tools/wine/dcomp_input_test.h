// Independent private ABI call and real wineserver-object lifetime checks.
// No implementation header or exported test hooks are used here.
static void input_binding_run(HMODULE module)
{
    SharedApi api(module);
    Ref<IDCompositionDesktopDevice> device;
    Ref<IDCompositionVisual2> visual, root;
    Ref<IDCompositionTarget> target, publisher;
    HR(api.device(nullptr, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&device.p)));
    HR(device->CreateVisual(&visual.p));
    HR(device->CreateVisual(&root.p));
    auto set = [](IUnknown *value, HANDLE sink) -> HRESULT
    {
        Ref<IUnknown> restricted;
        HRESULT hr = value->QueryInterface(layout_iid, reinterpret_cast<void **>(&restricted.p));
        if (FAILED(hr)) return hr;
        using Set = HRESULT(WINAPI *)(IUnknown *, HANDLE);
        return reinterpret_cast<Set>((*reinterpret_cast<void ***>(restricted.p))[35])(restricted.p, sink);
    };
    using Create = NTSTATUS(WINAPI *)(const void *, HANDLE *);
    using Query = NTSTATUS(WINAPI *)(HANDLE, OBJECT_INFORMATION_CLASS, void *, ULONG, ULONG *);
    auto create = reinterpret_cast<Create>(reinterpret_cast<void *>(GetProcAddress(
        GetModuleHandleW(L"win32u.dll"), "NtCreateCompositionInputSink")));
    auto query = reinterpret_cast<Query>(reinterpret_cast<void *>(GetProcAddress(
        GetModuleHandleW(L"ntdll.dll"), "NtQueryObject")));
    CHECK(create && query);
    if (!create || !query) return;
    auto handles = [query](HANDLE sink)
    {
        ULONG info[16] = {};
        CHECK(query(sink, ObjectBasicInformation, info, sizeof(info), nullptr) == 0);
        return info[2]; // genuine per-object count, not stubbed process statistics
    };
    struct Route { UINT type, reserved; UINT64 window, data[5]; };
    struct Descriptor { UINT size, view; Route routes[5]; UINT flags, reserved; };
    static_assert(sizeof(Descriptor) == 0x128, "independent x64 input descriptor");
    Window window;
    CHECK(window.handle);
    if (!window.handle) return;
    Descriptor desc = {};
    desc.size = 0xe8;
    for (unsigned i = 0; i < 4; ++i)
    {
        desc.routes[i].type = 2;
        desc.routes[i].window = reinterpret_cast<UINT_PTR>(window.handle);
    }
    Handle first, observer, second, denied, event, shared;
    CHECK(create(&desc, &first.value) == 0);
    CHECK(create(&desc, &second.value) == 0);
    if (!first.value || !second.value) return;
    CHECK(DuplicateHandle(GetCurrentProcess(), first.value, GetCurrentProcess(), &observer.value,
                          0, FALSE, DUPLICATE_SAME_ACCESS));
    CHECK(handles(observer.value) == 2);
    HR(set(visual.p, first.value));
    CHECK(handles(observer.value) == 3);
    first.reset(); // callers may close the original before Commit
    CHECK(handles(observer.value) == 2);
    HR(set(visual.p, observer.value));
    CHECK(handles(observer.value) == 2); // identity is LUID, not numeric handle
    HR(device->Commit());
    CHECK(handles(observer.value) == 2);
    HR(set(visual.p, second.value));
    CHECK(handles(observer.value) == 2); // last committed association still owns A
    CHECK(handles(second.value) == 2);
    event.value = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    CHECK(event.value);
    CHECK(FAILED(set(visual.p, event.value))); // wrong kernel object type
    CHECK(SetEvent(event.value)); // rejection must never close the caller's handle
    CHECK(FAILED(set(visual.p, INVALID_HANDLE_VALUE)));
    CHECK(FAILED(set(visual.p, reinterpret_cast<HANDLE>(static_cast<UINT_PTR>(0xdeadbeec)))));
    CHECK(DuplicateHandle(GetCurrentProcess(), second.value, GetCurrentProcess(), &denied.value,
                          0, FALSE, 0));
    CHECK(set(visual.p, denied.value) == E_ACCESSDENIED);
    denied.reset();
    CHECK(handles(second.value) == 2); // failed assignments retain B without leaks
    HR(device->Commit());
    CHECK(handles(observer.value) == 1);
    CHECK(handles(second.value) == 2);
    HR(set(visual.p, nullptr));
    CHECK(handles(second.value) == 2); // detach is pending until Commit
    HR(device->Commit());
    CHECK(handles(second.value) == 1);

    SetEnvironmentVariableW(L"GHUB_DCOMP_POPUP_TARGET", L"1");
    HR(device->CreateTargetForHwnd(window.handle, TRUE, &target.p));
    HR(target->SetRoot(root.p));
    HR(root->AddVisual(visual.p, TRUE, nullptr));
    HR(set(visual.p, observer.value));
    HR(device->Commit());
    HR(root->SetClip(D2D_RECT_F{0, 0, 0, 0}));
    HR(device->Commit());
    HR(target->SetRoot(nullptr));
    HR(api.shared(&shared.value));
    HR(api.open(device.p, shared.value, shared_target_iid, reinterpret_cast<void **>(&publisher.p)));
    HR(publisher->SetRoot(root.p));
    HR(device->Commit());
    HR(publisher->SetRoot(nullptr));
    HR(set(visual.p, nullptr));
    HR(device->Commit());
    CHECK(handles(observer.value) == 1);
    HR(root->RemoveVisual(visual.p));
    HR(set(visual.p, observer.value));
    HR(device->Commit());
    HR(set(visual.p, second.value));
    // Destruction must release different pending and committed bindings even
    // when the application's public device reference was released first.
    target.reset();
    publisher.reset();
    root.reset();
    device.reset();
    visual.reset();
    CHECK(handles(observer.value) == 1 && handles(second.value) == 1);
    std::fputs("Composition input identity, pending/committed lifetime and snapshot lifetime checked\n", stderr);
}

static void input_publication_pixels(HMODULE module)
{
    SharedApi api(module);
    Ref<ID3D11Device> d3d;
    Ref<ID3D11DeviceContext> context;
    Ref<IDXGIDevice> dxgi;
    Ref<IDCompositionDesktopDevice> producer, consumer;
    Ref<IDCompositionSurface> surface;
    Ref<IDCompositionVisual2> source, bound, window_root;
    Ref<IDCompositionVisual> imported;
    Ref<IDCompositionTarget> publisher, destination, blocked;
    Handle shared, sink;
    HR(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                         nullptr, 0, D3D11_SDK_VERSION, &d3d.p, nullptr, &context.p));
    HR(d3d->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void **>(&dxgi.p)));
    HR(api.device(dxgi.p, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&producer.p)));
    HR(api.device(nullptr, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&consumer.p)));
    HR(producer->CreateSurface(4, 4, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_ALPHA_MODE_PREMULTIPLIED, &surface.p));
    HR(producer->CreateVisual(&source.p));
    HR(producer->CreateVisual(&bound.p));
    HR(producer->CreateVisual(&window_root.p));
    HR(source->SetContent(surface.p));
    HR(api.shared(&shared.value));
    HR(api.open(producer.p, shared.value, shared_target_iid, reinterpret_cast<void **>(&publisher.p)));
    HR(publisher->SetRoot(source.p));
    HR(api.open(consumer.p, shared.value, shared_visual_iid, reinterpret_cast<void **>(&imported.p)));
    HR(imported->SetOffsetX(16));
    HR(imported->SetOffsetY(16));
    Window window;
    HR(consumer->CreateTargetForHwnd(window.handle, TRUE, &destination.p));
    HR(destination->SetRoot(imported.p));
    HR(fill(surface.p, d3d.p, context.p, nullptr, 0, 0, 1, 1));
    HR(producer->Commit());
    HR(consumer->Commit());
    pixel(window.handle, 1, 1, 0, 0, 255);
    struct Route { UINT type, reserved; UINT64 window, data[5]; };
    struct Descriptor { UINT size, view; Route routes[5]; UINT flags, reserved; } desc = {};
    desc.size = sizeof(desc);
    desc.routes[0].type = 1; // unsupported, nonuniform routing category
    using Create = NTSTATUS(WINAPI *)(const void *, HANDLE *);
    auto create = reinterpret_cast<Create>(reinterpret_cast<void *>(GetProcAddress(
        GetModuleHandleW(L"win32u.dll"), "NtCreateCompositionInputSink")));
    CHECK(create && create(&desc, &sink.value) == 0);
    if (!sink.value) return;
    Ref<IUnknown> restricted;
    HR(bound->QueryInterface(layout_iid, reinterpret_cast<void **>(&restricted.p)));
    using Set = HRESULT(WINAPI *)(IUnknown *, HANDLE);
    auto set = reinterpret_cast<Set>((*reinterpret_cast<void ***>(restricted.p))[35]);
    HR(set(restricted.p, sink.value));
    HR(source->AddVisual(bound.p, TRUE, nullptr));
    HR(fill(surface.p, d3d.p, context.p, nullptr, 1, 0, 0, 1));
    CHECK(producer->Commit() == HRESULT_FROM_WIN32(ERROR_NOT_SUPPORTED));
    HR(consumer->Commit());
    pixel(window.handle, 1, 1, 0, 0, 255); // shared publication rejected before frame write
    HR(source->RemoveVisual(bound.p));
    HR(window_root->AddVisual(bound.p, TRUE, nullptr));
    // Use an invisible second window: neither size nor visibility bypasses the guard.
    HWND hidden = CreateWindowExW(0, L"STATIC", L"input guard", WS_POPUP, 0, 0, 0, 0,
                                  nullptr, nullptr, GetModuleHandleW(nullptr), nullptr);
    CHECK(hidden);
    HR(producer->CreateTargetForHwnd(hidden, TRUE, &blocked.p));
    HR(blocked->SetRoot(window_root.p));
    CHECK(producer->Commit() == HRESULT_FROM_WIN32(ERROR_NOT_SUPPORTED));
    HR(consumer->Commit());
    pixel(window.handle, 1, 1, 0, 0, 255); // local guard also precedes unrelated shared writes
    HR(set(restricted.p, nullptr));
    HR(producer->Commit());
    HR(consumer->Commit());
    pixel(window.handle, 1, 1, 255, 0, 0);
    blocked.reset();
    CHECK(DestroyWindow(hidden));
    std::fputs("Composition input guards preserve previously published pixels\n", stderr);
}
