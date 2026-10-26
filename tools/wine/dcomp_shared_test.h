// Included by the window-pixel regression to reuse the D3D and capture helpers.
static const IID shared_partner_iid = {0xd14b6158, 0xc3fa, 0x4bce, {0x9c, 0x1f, 0xb6, 0x1d, 0x86, 0x65, 0xea, 0xb0}};
static const IID shared_visual_iid = {0x4d93059d, 0x097b, 0x4651, {0x9a, 0x60, 0xf0, 0xf2, 0x51, 0x16, 0xe2, 0xf3}};
static const IID shared_target_iid = {0xeacdd04c, 0x117e, 0x4e17, {0x88, 0xf4, 0xd1, 0xb1, 0x2b, 0x0e, 0x3d, 0x89}};
struct Handle
{
    HANDLE value = nullptr;
    ~Handle() { reset(); }
    void reset()
    {
        if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value);
        value = nullptr;
    }
};
struct SharedApi
{
    using Create = HRESULT(WINAPI *)(IUnknown *, REFIID, void **);
    using Shared = HRESULT(WINAPI *)(HANDLE *);
    using Open = HRESULT(WINAPI *)(IUnknown *, HANDLE, REFIID, void **);
    using Target = HRESULT(WINAPI *)(HANDLE, IDCompositionTarget **);
    Create device = nullptr;
    Shared shared = nullptr;
    Open wrapped = nullptr;
    Target target = nullptr;
    HMODULE framework = nullptr;
    explicit SharedApi(HMODULE module)
    {
        device = reinterpret_cast<Create>(reinterpret_cast<void *>(GetProcAddress(module, "DCompositionCreateDevice2")));
        shared = reinterpret_cast<Shared>(reinterpret_cast<void *>(GetProcAddress(module, MAKEINTRESOURCEA(1040))));
        target = reinterpret_cast<Target>(reinterpret_cast<void *>(GetProcAddress(module, MAKEINTRESOURCEA(1038))));
        char path[MAX_PATH];
        if (GetEnvironmentVariableA("GHUB_DCOMP_FRAMEWORK_PATH", path, MAX_PATH))
        {
            framework = LoadLibraryExA(path, nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
            CHECK(framework);
            if (framework)
            {
                shared = reinterpret_cast<Shared>(reinterpret_cast<void *>(GetProcAddress(framework, "DCompPrivates_CreateSharedVisualHandle")));
                wrapped = reinterpret_cast<Open>(reinterpret_cast<void *>(GetProcAddress(framework, "DCompPrivates_OpenSharedDCompositionObject")));
                CHECK(wrapped);
            }
        }
        CHECK(device && shared && target);
    }
    ~SharedApi() { if (framework) FreeLibrary(framework); }
    HRESULT open(IUnknown *owner, HANDLE handle, REFIID iid, void **out)
    {
        if (wrapped) return wrapped(owner, handle, iid, out);
        Ref<IUnknown> partner;
        HRESULT hr = owner->QueryInterface(shared_partner_iid, reinterpret_cast<void **>(&partner.p));
        if (FAILED(hr)) return hr;
        auto method = reinterpret_cast<Open>((*reinterpret_cast<void ***>(partner.p))[29]);
        return method(partner.p, handle, iid, out);
    }
};

static void shared_child(HMODULE module, HANDLE handle)
{
    SharedApi api(module);
    if (!api.device || !api.target) return;
    Ref<ID3D11Device> d3d;
    Ref<ID3D11DeviceContext> context;
    Ref<IDXGIDevice> dxgi;
    Ref<IDCompositionDesktopDevice> producer;
    Ref<IDCompositionSurface> surface;
    Ref<IDCompositionVisual2> root;
    Ref<IDCompositionTarget> target;
    HR(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                         nullptr, 0, D3D11_SDK_VERSION, &d3d.p, nullptr, &context.p));
    HR(d3d->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void **>(&dxgi.p)));
    HR(api.device(dxgi.p, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&producer.p)));
    HR(producer->CreateSurface(4, 4, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_ALPHA_MODE_PREMULTIPLIED, &surface.p));
    HR(producer->CreateVisual(&root.p));
    HR(root->SetContent(surface.p));
    HR(api.target(handle, &target.p));
    CHECK(CloseHandle(handle)); // section ownership moved into target
    HR(target->SetRoot(root.p));
    HR(fill(surface.p, d3d.p, context.p, nullptr, 0, 0, 1, 1));
    HR(producer->Commit());
    std::fputs("Child process published blue shared frame\n", stderr);
}

static DWORD WINAPI shared_commit_worker(void *arg)
{
    return static_cast<IDCompositionDesktopDevice *>(arg)->Commit();
}

// A -> B -> window, with publication from another thread and pending consumer
// mutations. The consumer's committed tree must survive removal/release of its
// original imported visual; local EndDraw pixels must remain pending too.
static void shared_refresh_run(HMODULE module)
{
    SharedApi api(module);
    Handle a, b, worker;
    Ref<ID3D11Device> d3d;
    Ref<ID3D11DeviceContext> context;
    Ref<IDXGIDevice> dxgi;
    Ref<IDCompositionDesktopDevice> producer, relay, consumer;
    Ref<IDCompositionSurface> surface, local_surface;
    Ref<IDCompositionVisual2> source, root, local;
    Ref<IDCompositionVisual> intermediate, imported;
    Ref<IDCompositionTarget> publish_a, publish_b, target;
    HR(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                         nullptr, 0, D3D11_SDK_VERSION, &d3d.p, nullptr, &context.p));
    HR(d3d->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void **>(&dxgi.p)));
    HR(api.device(dxgi.p, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&producer.p)));
    HR(api.device(nullptr, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&relay.p)));
    HR(api.device(dxgi.p, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&consumer.p)));
    HR(api.shared(&a.value)); HR(api.shared(&b.value));
    HR(producer->CreateSurface(4,4,DXGI_FORMAT_B8G8R8A8_UNORM,DXGI_ALPHA_MODE_PREMULTIPLIED,&surface.p));
    HR(producer->CreateVisual(&source.p)); HR(source->SetContent(surface.p));
    HR(api.open(producer.p,a.value,shared_target_iid,reinterpret_cast<void **>(&publish_a.p)));
    HR(publish_a->SetRoot(source.p));
    HR(api.open(relay.p,a.value,shared_visual_iid,reinterpret_cast<void **>(&intermediate.p)));
    HR(api.open(relay.p,b.value,shared_target_iid,reinterpret_cast<void **>(&publish_b.p)));
    HR(publish_b->SetRoot(intermediate.p)); HR(relay->Commit());
    HR(api.open(consumer.p,b.value,shared_visual_iid,reinterpret_cast<void **>(&imported.p)));
    a.reset(); b.reset();
    HR(consumer->CreateVisual(&root.p)); HR(consumer->CreateVisual(&local.p));
    HR(consumer->CreateSurface(4,4,DXGI_FORMAT_B8G8R8A8_UNORM,DXGI_ALPHA_MODE_PREMULTIPLIED,&local_surface.p));
    HR(fill(local_surface.p,d3d.p,context.p,nullptr,0,0,1,1));
    HR(local->SetContent(local_surface.p)); HR(local->SetOffsetX(8));
    HR(root->SetOffsetX(16)); HR(root->SetOffsetY(16));
    HR(root->AddVisual(imported.p,TRUE,nullptr)); HR(root->AddVisual(local.p,TRUE,nullptr));
    Window window;
    HR(consumer->CreateTargetForHwnd(window.handle,TRUE,&target.p));
    HR(target->SetRoot(root.p)); HR(consumer->Commit());
    static const IID batch_iid = {0x25682ec4,0x73a4,0x4022,{0xa0,0x4f,0x1b,0xbe,0x6d,0xc3,0xe1,0xf0}};
    static const IID event_iid = {0x6b556968,0x2d25,0x11e2,{0x99,0xf6,0x8b,0xc3,0x60,0x88,0x70,0x9b}};
    Ref<IUnknown> batch, event_iface;
    Handle completed;
    completed.value = CreateEventW(nullptr,FALSE,FALSE,nullptr); CHECK(completed.value);
    HR(consumer->QueryInterface(batch_iid,reinterpret_cast<void **>(&batch.p)));
    HR(consumer->QueryInterface(event_iid,reinterpret_cast<void **>(&event_iface.p)));
    using Batch = HRESULT(WINAPI *)(IUnknown *, DWORD *);
    using Completion = HRESULT(WINAPI *)(IUnknown *, HANDLE);
    auto current = reinterpret_cast<Batch>((*reinterpret_cast<void ***>(batch.p))[55]);
    auto completion = reinterpret_cast<Completion>((*reinterpret_cast<void ***>(event_iface.p))[8]);
    DWORD before = 0, after = 0;
    HR(current(batch.p,&before)); HR(completion(event_iface.p,completed.value));
    HR(fill(surface.p,d3d.p,context.p,nullptr,1,0,0,1)); HR(producer->Commit());
    pixel(window.handle,1,1,255,0,0); pixel(window.handle,9,1,0,0,255);

    // Retain all consumer pending edits across producer-driven refresh.
    HR(root->RemoveVisual(imported.p)); imported.reset();
    HR(root->SetOffsetX(32)); HR(target->SetRoot(nullptr));
    HR(fill(local_surface.p,d3d.p,context.p,nullptr,0,1,0,1));
    HR(fill(surface.p,d3d.p,context.p,nullptr,0,1,0,1));
    worker.value = CreateThread(nullptr,0,shared_commit_worker,producer.p,0,nullptr);
    CHECK(worker.value);
    if (worker.value)
    {
        // No message pumping while waiting: publication must never block on
        // the consumer apartment to process the notification.
        CHECK(WaitForSingleObject(worker.value,5000) == WAIT_OBJECT_0);
        DWORD result = E_FAIL; CHECK(GetExitCodeThread(worker.value,&result) && result == S_OK);
        worker.reset();
    }
    pixel(window.handle,1,1,0,255,0); pixel(window.handle,9,1,0,0,255);
    pixel(window.handle,25,1,255,255,255);
    HR(current(batch.p,&after)); CHECK(after == before);
    CHECK(WaitForSingleObject(completed.value,0) == WAIT_TIMEOUT);
    HR(consumer->Commit()); pixel(window.handle,1,1,255,255,255);
    HR(current(batch.p,&after)); CHECK(after == before + 1);
    CHECK(WaitForSingleObject(completed.value,0) == WAIT_OBJECT_0);
    HR(completion(event_iface.p,nullptr)); batch.reset(); event_iface.reset();
    HR(target->SetRoot(root.p)); HR(consumer->Commit());
    pixel(window.handle,25,1,0,255,0); // pending local surface and layout now accepted

    // Tear down subscribers before draining another publication. No queued
    // raw pointer may survive destruction and no dispatch reference may leak.
    HR(fill(surface.p,d3d.p,context.p,nullptr,0,0,1,1)); HR(producer->Commit());
    target.reset(); root.reset(); local.reset(); local_surface.reset(); consumer.reset();
    publish_b.reset(); intermediate.reset(); relay.reset();
    pump();
    std::fputs("Shared refresh: threaded publication, chain, pending state and queued teardown checked\n",stderr);
}

static DWORD WINAPI shared_release_worker(void *arg)
{
    return static_cast<IDCompositionDesktopDevice *>(arg)->Release();
}

static void shared_endpoint_lifetime_run(HMODULE module)
{
    SharedApi api(module);
    using Unload = HRESULT(WINAPI *)();
    auto unload = reinterpret_cast<Unload>(reinterpret_cast<void *>(GetProcAddress(module,"DllCanUnloadNow")));
    Ref<IDCompositionDesktopDevice> device;
    HR(api.device(nullptr,__uuidof(IDCompositionDesktopDevice),reinterpret_cast<void **>(&device.p)));
    Handle worker;
    worker.value = CreateThread(nullptr,0,shared_release_worker,device.p,0,nullptr);
    CHECK(worker.value);
    if (!worker.value) return;
    device.p = nullptr; // the worker consumes the one application reference
    CHECK(WaitForSingleObject(worker.value,5000) == WAIT_OBJECT_0);
    DWORD refs = 1; CHECK(GetExitCodeThread(worker.value,&refs) && refs == 0);
    CHECK(unload && unload() == S_FALSE); // callback code retained until WM_CLOSE
    pump(); CHECK(unload && unload() == S_OK);
    std::fputs("Shared refresh endpoint: off-thread final release and DLL lifetime checked\n",stderr);
}

static void shared_run(HMODULE module)
{
    SharedApi api(module);
    if (!api.device || !api.shared || !api.target) return;
    Handle handle, duplicate;
    HR(api.shared(&handle.value));
    CHECK(handle.value);
    CHECK(DuplicateHandle(GetCurrentProcess(), handle.value, GetCurrentProcess(), &duplicate.value,
                          0, FALSE, DUPLICATE_SAME_ACCESS));
    handle.reset();
    Ref<ID3D11Device> d3d;
    Ref<ID3D11DeviceContext> context;
    Ref<IDXGIDevice> dxgi;
    Ref<IDCompositionDesktopDevice> producer, consumer;
    Ref<IDCompositionSurface> surface;
    Ref<IDCompositionVisual2> root;
    Ref<IDCompositionVisual> imported, feedback, invalid;
    Ref<IDCompositionTarget> publisher, rejected, destination;
    HR(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                         nullptr, 0, D3D11_SDK_VERSION, &d3d.p, nullptr, &context.p));
    HR(d3d->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void **>(&dxgi.p)));
    HR(api.device(dxgi.p, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&producer.p)));
    HR(api.device(nullptr, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&consumer.p)));
    HR(producer->CreateSurface(4, 4, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_ALPHA_MODE_PREMULTIPLIED, &surface.p));
    HR(producer->CreateVisual(&root.p));
    HR(root->SetContent(surface.p));
    HR(api.open(producer.p, duplicate.value, shared_target_iid, reinterpret_cast<void **>(&publisher.p)));
    CHECK(api.target(duplicate.value, &rejected.p) == HRESULT_FROM_WIN32(ERROR_BUSY) && !rejected.p);
    HR(api.open(consumer.p, duplicate.value, shared_visual_iid, reinterpret_cast<void **>(&imported.p)));
    HR(api.open(producer.p, duplicate.value, shared_visual_iid, reinterpret_cast<void **>(&feedback.p)));
    CHECK(FAILED(publisher->SetRoot(feedback.p)));
    HR(root->AddVisual(feedback.p, TRUE, nullptr));
    CHECK(FAILED(publisher->SetRoot(root.p)));
    HR(root->RemoveVisual(feedback.p));
    feedback.reset();
    Handle readonly;
    CHECK(DuplicateHandle(GetCurrentProcess(), duplicate.value, GetCurrentProcess(), &readonly.value,
                          FILE_MAP_READ, FALSE, 0));
    CHECK(FAILED(api.open(consumer.p, readonly.value, shared_visual_iid, reinterpret_cast<void **>(&invalid.p))) && !invalid.p);
    readonly.reset();
    duplicate.reset(); // both endpoints must survive all original handles closing
    HR(publisher->SetRoot(root.p));
    HR(imported->SetOffsetX(16.0f));
    HR(imported->SetOffsetY(16.0f));
    Handle event;
    event.value = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    CHECK(FAILED(api.open(consumer.p, event.value, shared_visual_iid, reinterpret_cast<void **>(&invalid.p))) && !invalid.p);
    Handle short_section;
    short_section.value = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, 4096, nullptr);
    CHECK(FAILED(api.open(consumer.p, short_section.value, shared_visual_iid, reinterpret_cast<void **>(&invalid.p))) && !invalid.p);
    CHECK(FAILED(api.open(consumer.p, INVALID_HANDLE_VALUE, shared_visual_iid, reinterpret_cast<void **>(&invalid.p))) && !invalid.p);
    Window window;
    CHECK(window.handle);
    if (!window.handle) return;
    pump();
    SetEnvironmentVariableW(L"GHUB_DCOMP_POPUP_TARGET", L"1");
    HR(consumer->CreateTargetForHwnd(window.handle, TRUE, &destination.p));
    HR(destination->SetRoot(imported.p));
    HR(fill(surface.p, d3d.p, context.p, nullptr, 1, 0, 0, 1));
    HR(consumer->Commit());
    pixel(window.handle, 1, 1, 255, 255, 255);
    HR(producer->Commit());
    pixel(window.handle, 1, 1, 255, 0, 0); // producer-only commit refreshes same-process consumer
    HR(consumer->Commit());
    pixel(window.handle, 1, 1, 255, 0, 0);
    HR(fill(surface.p, d3d.p, context.p, nullptr, 0, 1, 0, 1, false));
    HR(producer->Commit());
    HR(consumer->Commit());
    pixel(window.handle, 1, 1, 255, 0, 0);
    HR(surface->EndDraw());
    HR(consumer->Commit());
    pixel(window.handle, 1, 1, 255, 0, 0);
    HR(producer->Commit());
    pixel(window.handle, 1, 1, 0, 255, 0); // published update is visible before consumer Commit
    HR(consumer->Commit());
    pixel(window.handle, 1, 1, 0, 255, 0);
    HR(publisher->SetRoot(nullptr));
    // A consumer window on the producer's own device sees the current batch.
    Handle same;
    Ref<IDCompositionVisual> same_visual;
    Ref<IDCompositionTarget> same_publisher, same_window;
    HR(api.shared(&same.value));
    HR(api.open(producer.p, same.value, shared_target_iid, reinterpret_cast<void **>(&same_publisher.p)));
    HR(api.open(producer.p, same.value, shared_visual_iid, reinterpret_cast<void **>(&same_visual.p)));
    same.reset();
    HR(same_publisher->SetRoot(root.p));
    HR(same_visual->SetOffsetX(16.0f));
    HR(same_visual->SetOffsetY(16.0f));
    HR(destination->SetRoot(nullptr));
    HR(consumer->Commit());
    destination.reset();
    HR(producer->CreateTargetForHwnd(window.handle, TRUE, &same_window.p));
    HR(same_window->SetRoot(same_visual.p));
    HR(fill(surface.p, d3d.p, context.p, nullptr, 1, 0, 0, 1));
    HR(producer->Commit());
    pixel(window.handle, 1, 1, 255, 0, 0);
    HR(fill(surface.p, d3d.p, context.p, nullptr, 0, 1, 0, 1));
    HR(producer->Commit());
    pixel(window.handle, 1, 1, 0, 255, 0);
    HR(same_window->SetRoot(nullptr));
    HR(producer->Commit());
    same_window.reset();
    same_publisher.reset();
    same_visual.reset();
    HR(publisher->SetRoot(root.p));
    HR(producer->Commit());
    HR(consumer->CreateTargetForHwnd(window.handle, TRUE, &destination.p));
    HR(destination->SetRoot(imported.p));
    HR(consumer->Commit());
    pixel(window.handle, 1, 1, 0, 255, 0);
    HR(root->SetOffsetX(-1.0f));
    HR(producer->Commit());
    HR(consumer->Commit());
    pixel(window.handle, 2, 1, 0, 255, 0);
    pixel(window.handle, 3, 1, 255, 255, 255);
    HR(publisher->SetRoot(nullptr));
    HR(consumer->Commit());
    pixel(window.handle, 1, 1, 0, 255, 0);
    HR(producer->Commit());
    HR(consumer->Commit());
    pixel(window.handle, 1, 1, 255, 255, 255);
    HR(publisher->SetRoot(root.p));
    HR(producer->Commit());
    publisher.reset();
    root.reset();
    surface.reset();
    producer.reset();
    HR(consumer->Commit());
    pixel(window.handle, 1, 1, 0, 255, 0); // last committed frame survives publisher destruction

    Handle cross;
    HR(api.shared(&cross.value));
    imported.reset();
    HR(api.open(consumer.p, cross.value, shared_visual_iid, reinterpret_cast<void **>(&imported.p)));
    HR(imported->SetOffsetX(16.0f));
    HR(imported->SetOffsetY(16.0f));
    HR(destination->SetRoot(imported.p));
    HR(consumer->Commit());
    pixel(window.handle, 1, 1, 255, 255, 255);
    CHECK(SetHandleInformation(cross.value, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT));
    WCHAR executable[MAX_PATH], command[MAX_PATH + 100];
    CHECK(GetModuleFileNameW(nullptr, executable, MAX_PATH));
    swprintf(command, MAX_PATH + 100, L"\"%ls\" --shared-child %llu", executable,
             static_cast<unsigned long long>(reinterpret_cast<ULONG_PTR>(cross.value)));
    STARTUPINFOW startup = {};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION child = {};
    CHECK(CreateProcessW(executable, command, nullptr, nullptr, TRUE, 0, nullptr, nullptr, &startup, &child));
    if (child.hProcess)
    {
        cross.reset(); // only child and consumer now own the resource
        DWORD wait = WaitForSingleObject(child.hProcess, 45000), code = 1;
        CHECK(wait == WAIT_OBJECT_0);
        if (wait != WAIT_OBJECT_0) { TerminateProcess(child.hProcess, 1); WaitForSingleObject(child.hProcess, 5000); }
        CHECK(GetExitCodeProcess(child.hProcess, &code) && code == 0);
        CloseHandle(child.hThread);
        CloseHandle(child.hProcess);
        HR(consumer->Commit());
        pixel(window.handle, 1, 1, 0, 0, 255);
    }
    HR(destination->SetRoot(nullptr));
    HR(consumer->Commit());
}
