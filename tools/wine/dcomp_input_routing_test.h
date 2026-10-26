// Public visual operations and real SendInput delivery; no compositor test hooks.
static unsigned routed_clicks[3];
static unsigned routed_cursors[3], routed_activations[3];
static bool pointer_mouse_test;
static unsigned mouse_pointer_down, mouse_pointer_up;
static DWORD WINAPI cross_thread_device_rects(void *device)
{
    RECT device_rect = {}, display_rect = {};
    POINTER_INFO no_sample = {};
    CHECK(!GetPointerInfo(1, &no_sample)); // This thread has never received a pointer.
    CHECK(GetPointerDeviceRects(device, &device_rect, &display_rect));
    CHECK(EqualRect(&device_rect, &display_rect));
    CHECK(display_rect.right > display_rect.left && display_rect.bottom > display_rect.top);
    CHECK(!GetPointerDeviceRects(reinterpret_cast<HANDLE>(0x1234), &device_rect, &display_rect));
    CHECK(GetLastError() == ERROR_INVALID_HANDLE);
    return 0;
}
static LRESULT CALLBACK route_proc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam)
{
    unsigned index = GetWindowLongPtrW(hwnd, GWLP_USERDATA);
    if (message == WM_SETCURSOR)
    {
        ++routed_cursors[index];
        SetCursor(LoadCursorW(nullptr, MAKEINTRESOURCEW(index ? 32513 : 32512)));
        return TRUE;
    }
    if (message == WM_POINTERDOWN || message == WM_POINTERUP)
    {
        POINTER_INFO info = {}, history = {};
        CHECK(pointer_mouse_test && index != 0);
        CHECK(GetPointerInfo(GET_POINTERID_WPARAM(wparam), &info));
        CHECK(info.pointerType == PT_MOUSE && info.hwndTarget == hwnd);
        CHECK(info.pointerId == 1 && info.sourceDevice && info.historyCount == 1);
        CHECK(info.ptPixelLocation.x == (short)LOWORD(lparam) && info.ptPixelLocation.y == (short)HIWORD(lparam));
        CHECK(info.PerformanceCount != 0);
        CHECK(info.pointerFlags & (message == WM_POINTERDOWN ? POINTER_FLAG_DOWN : POINTER_FLAG_UP));
        CHECK(info.ButtonChangeType == (message == WM_POINTERDOWN ? POINTER_CHANGE_FIRSTBUTTON_DOWN : POINTER_CHANGE_FIRSTBUTTON_UP));
        UINT32 count = 1;
        CHECK(GetPointerInfoHistory(info.pointerId, &count, &history));
        CHECK(count == 1 && !std::memcmp(&info, &history, sizeof(info)));
        CHECK(!GetPointerInfo(0, &history) && GetLastError() == ERROR_INVALID_PARAMETER);
        if (message == WM_POINTERDOWN)
        {
            ++routed_clicks[index]; ++mouse_pointer_down;
            HANDLE thread = CreateThread(nullptr,0,cross_thread_device_rects,info.sourceDevice,0,nullptr);
            CHECK(thread);
            if (thread) { CHECK(WaitForSingleObject(thread,5000) == WAIT_OBJECT_0); CloseHandle(thread); }
        }
        else ++mouse_pointer_up;
        return 0;
    }
    if (message == WM_LBUTTONDOWN || message == WM_LBUTTONDBLCLK)
    {
        ++routed_clicks[GetWindowLongPtrW(hwnd, GWLP_USERDATA)];
        return 0;
    }
    if (message == WM_LBUTTONUP) return 0;
    if (message == WM_MOUSEACTIVATE) { ++routed_activations[index]; return MA_NOACTIVATE; }
    return DefWindowProcW(hwnd, message, wparam, lparam);
}
static void input_routing_child(HMODULE module, HANDLE shared)
{
    SharedApi api(module);
    Ref<IDCompositionDesktopDevice> device;
    Ref<IDCompositionVisual> visual;
    Ref<IDCompositionTarget> target;
    Window window;
    CHECK(SetWindowPos(window.handle, HWND_TOP, 150, 40, 64, 64, SWP_NOACTIVATE));
    HR(api.device(nullptr, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&device.p)));
    HR(api.open(device.p, shared, shared_visual_iid, reinterpret_cast<void **>(&visual.p)));
    CHECK(CloseHandle(shared));
    HR(device->CreateTargetForHwnd(window.handle, TRUE, &target.p));
    HR(target->SetRoot(visual.p)); HR(device->Commit()); pump();
    POINT pt{20,20}; CHECK(ClientToScreen(window.handle, &pt)); CHECK(SetCursorPos(pt.x,pt.y)); pump();
    INPUT events[2] = {}; events[0].type = events[1].type = INPUT_MOUSE;
    events[0].mi.dwFlags = MOUSEEVENTF_LEFTDOWN; events[1].mi.dwFlags = MOUSEEVENTF_LEFTUP;
    CHECK(SendInput(2, events, sizeof(INPUT)) == 2); pump();
}
static LONG pointer_deliveries, pointer_callbacks;
static HWND pointer_destination;
static LRESULT CALLBACK pointer_proc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam)
{
    if (message == WM_POINTERENTER)
    {
        if (wparam == 0x321) InterlockedIncrement(&pointer_deliveries);
        return 0;
    }
    return DefWindowProcW(hwnd, message, wparam, lparam);
}
static DWORD WINAPI pointer_worker(void *arg)
{
    HANDLE ready = static_cast<HANDLE>(arg);
    pointer_destination = CreateWindowExW(0, L"STATIC", L"pointer destination", 0, 0, 0, 0, 0,
                                           HWND_MESSAGE, nullptr, GetModuleHandleW(nullptr), nullptr);
    if (pointer_destination)
        SetWindowLongPtrW(pointer_destination, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(pointer_proc));
    SetEvent(ready);
    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) DispatchMessageW(&msg);
    if (pointer_destination) DestroyWindow(pointer_destination);
    return 0;
}
static ULONG_PTR WINAPI routed_delegate(const MSG *msg, void *)
{
    CHECK(msg->hwnd == pointer_destination && msg->message == WM_POINTERENTER && msg->wParam == 0x321);
    InterlockedIncrement(&pointer_callbacks);
    return 1; // forward through the existing delegated hardware queue
}
static void input_routing_run(HMODULE module)
{
    WCHAR enabled[2];
    pointer_mouse_test = GetEnvironmentVariableW(L"WINE_GHUB_POINTER_MOUSE", enabled, 2) == 1 && enabled[0] == L'1';
    SharedApi api(module);
    Ref<IDCompositionDesktopDevice> producer, consumer;
    Ref<IDCompositionVisual2> root, front;
    Ref<IDCompositionVisual> imported;
    Ref<IDCompositionTarget> target, publisher;
    Handle sink[2], shared;
    Window window;
    CHECK(window.handle);
    if (!window.handle) return;
    SetWindowLongPtrW(window.handle, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(route_proc));
    HWND routes[2] = {};
    struct Windows { HWND *items; ~Windows() { for (unsigned i=0;i<2;++i) if (items[i]) DestroyWindow(items[i]); } } cleanup{routes};
    struct Route { UINT type, reserved; UINT64 window, data[5]; };
    struct Descriptor { UINT size, view; Route routes[5]; UINT flags, reserved; };
    using Create = NTSTATUS(WINAPI *)(const void *, HANDLE *);
    auto create = reinterpret_cast<Create>(reinterpret_cast<void *>(GetProcAddress(
        GetModuleHandleW(L"win32u.dll"), "NtCreateCompositionInputSink")));
    CHECK(create);
    if (!create) return;
    for (unsigned i = 0; i < 2; ++i)
    {
        routes[i] = CreateWindowExW(0, L"STATIC", L"composition sink", 0, 0, 0, 0, 0,
                                    HWND_MESSAGE, nullptr, GetModuleHandleW(nullptr), nullptr);
        CHECK(routes[i]);
        SetWindowLongPtrW(routes[i], GWLP_USERDATA, i + 1);
        SetWindowLongPtrW(routes[i], GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(route_proc));
        Descriptor desc = {}; desc.size = 0xe8;
        for (unsigned j = 0; j < 4; ++j)
        { desc.routes[j].type = 2; desc.routes[j].window = reinterpret_cast<UINT_PTR>(routes[i]); }
        CHECK(create(&desc, &sink[i].value) == 0);
        if (!sink[i].value) return;
    }
    auto set = [](IUnknown *visual, HANDLE handle) -> HRESULT
    {
        Ref<IUnknown> restricted;
        HRESULT hr = visual->QueryInterface(layout_iid, reinterpret_cast<void **>(&restricted.p));
        if (FAILED(hr)) return hr;
        using Setter = HRESULT(WINAPI *)(IUnknown *, HANDLE);
        return reinterpret_cast<Setter>((*reinterpret_cast<void ***>(restricted.p))[35])(restricted.p, handle);
    };
    auto size = [](IUnknown *visual, unsigned slot, float value) -> HRESULT
    {
        Ref<IUnknown> restricted;
        HRESULT hr = visual->QueryInterface(layout_iid, reinterpret_cast<void **>(&restricted.p));
        if (FAILED(hr)) return hr;
        using Setter = HRESULT(WINAPI *)(IUnknown *, float);
        return reinterpret_cast<Setter>((*reinterpret_cast<void ***>(restricted.p))[slot])(restricted.p, value);
    };
    auto click = [&](int x, int y, unsigned expected, const char *label)
    {
        std::memset(routed_cursors, 0, sizeof(routed_cursors));
        std::memset(routed_activations, 0, sizeof(routed_activations));
        POINT pt{x,y}; CHECK(ClientToScreen(window.handle, &pt));
        CHECK(SetCursorPos(pt.x, pt.y)); pump();
        std::memset(routed_clicks, 0, sizeof(routed_clicks));
        INPUT events[2] = {}; events[0].type = events[1].type = INPUT_MOUSE;
        events[0].mi.dwFlags = MOUSEEVENTF_LEFTDOWN; events[1].mi.dwFlags = MOUSEEVENTF_LEFTUP;
        CHECK(SendInput(2, events, sizeof(INPUT)) == 2); pump();
        bool ok = true;
        for (unsigned i = 0; i < 3; ++i) if (routed_clicks[i] != unsigned(i == expected)) ok = false;
        std::fprintf(stderr, "ROUTE %s: host=%u A=%u B=%u expected=%u %s\n", label,
                     routed_clicks[0], routed_clicks[1], routed_clicks[2], expected, ok ? "PASS" : "FAIL");
        CHECK(ok);
        CHECK((GetCapture() || routed_cursors[0] > 0) && !routed_cursors[1] && !routed_cursors[2]);
        CHECK(!routed_activations[1] && !routed_activations[2]);
    };
    HR(api.device(nullptr, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&producer.p)));
    HR(api.device(nullptr, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&consumer.p)));
    HR(producer->CreateVisual(&root.p)); HR(producer->CreateVisual(&front.p));
    HR(size(root.p, 36, 40)); HR(size(root.p, 37, 40));
    HR(set(root.p, sink[0].value));
    SetEnvironmentVariableW(L"GHUB_DCOMP_POPUP_TARGET", L"1");
    HR(producer->CreateTargetForHwnd(window.handle, TRUE, &target.p));
    HR(target->SetRoot(root.p)); HR(producer->Commit());
    click(20, 20, 1, "transparent sized visual");
    click(50, 50, 0, "outside visual");
    HR(set(root.p, sink[1].value));
    click(20, 20, 1, "pending assignment invisible");
    HR(producer->Commit()); click(20, 20, 2, "committed replacement");
    HR(root->SetOffsetX(10.5f)); HR(root->SetOffsetY(10.5f));
    HR(root->SetClip(D2D_RECT_F{0, 0, 20, 20})); HR(producer->Commit());
    click(10, 20, 0, "fractional left excluded");
    click(11, 20, 2, "fractional left included");
    click(30, 20, 2, "fractional right included");
    click(31, 20, 0, "fractional right excluded");
    HR(root->SetOffsetX(0.0f)); HR(root->SetOffsetY(0.0f)); HR(root->SetClip(static_cast<IDCompositionClip *>(nullptr)));
    HR(size(front.p, 36, 10)); HR(size(front.p, 37, 10));
    HR(front->SetOffsetX(15)); HR(front->SetOffsetY(15)); HR(set(front.p, sink[0].value));
    HR(root->AddVisual(front.p, TRUE, nullptr)); HR(producer->Commit());
    click(20, 20, 1, "front child overrides parent"); click(30, 30, 2, "parent outside child");
    CHECK(SetCapture(window.handle) == nullptr);
    click(20, 20, 0, "capture takes precedence"); CHECK(ReleaseCapture());
    HR(set(front.p, nullptr)); HR(producer->Commit()); click(20, 20, 2, "unbound child inherits parent");
    HR(api.shared(&shared.value));
    HR(api.open(producer.p, shared.value, shared_target_iid, reinterpret_cast<void **>(&publisher.p)));
    HR(publisher->SetRoot(root.p)); HR(target->SetRoot(nullptr)); HR(producer->Commit()); target.reset();
    HR(api.open(consumer.p, shared.value, shared_visual_iid, reinterpret_cast<void **>(&imported.p)));
    HR(imported->SetOffsetX(5)); HR(imported->SetOffsetY(5));
    HR(consumer->CreateTargetForHwnd(window.handle, TRUE, &target.p));
    HR(target->SetRoot(imported.p)); HR(consumer->Commit());
    click(20, 20, 2, "shared input-only snapshot");
    HR(set(root.p, sink[0].value)); HR(producer->Commit());
    click(20, 20, 1, "producer-only publication refresh");
    HR(consumer->Commit()); click(20, 20, 1, "explicit consumer commit after refresh");
    HR(imported->SetClip(D2D_RECT_F{0, 0, 0, 0}));
    HR(set(root.p, sink[1].value)); HR(producer->Commit());
    click(20, 20, 2, "refresh preserves pending consumer clip");
    HR(consumer->Commit()); click(20, 20, 0, "explicit consumer commit accepts clip");
    HR(imported->SetClip(static_cast<IDCompositionClip *>(nullptr))); HR(consumer->Commit());
    HR(set(root.p, sink[0].value)); HR(producer->Commit());
    sink[0].reset(); sink[1].reset();
    click(20, 20, 1, "caller handles closed");
    // A separate reader process receives the Section, never raw sink handles.
    CHECK(SetHandleInformation(shared.value, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT));
    WCHAR executable[MAX_PATH], command[MAX_PATH + 100];
    CHECK(GetModuleFileNameW(nullptr, executable, MAX_PATH));
    swprintf(command, MAX_PATH + 100, L"\"%ls\" --input-child %llu", executable,
             static_cast<unsigned long long>(reinterpret_cast<ULONG_PTR>(shared.value)));
    STARTUPINFOW startup = {}; startup.cb = sizeof(startup);
    PROCESS_INFORMATION child = {};
    std::memset(routed_clicks, 0, sizeof(routed_clicks));
    CHECK(CreateProcessW(executable, command, nullptr, nullptr, TRUE, 0, nullptr, nullptr, &startup, &child));
    if (child.hProcess)
    {
        DWORD start = GetTickCount(), code = 1;
        while (WaitForSingleObject(child.hProcess, 0) == WAIT_TIMEOUT && GetTickCount() - start < 20000) pump();
        CHECK(GetExitCodeProcess(child.hProcess, &code) && code == 0);
        if (code == STILL_ACTIVE) { TerminateProcess(child.hProcess, 1); WaitForSingleObject(child.hProcess, 5000); }
        CloseHandle(child.hThread); CloseHandle(child.hProcess); pump();
        CHECK(routed_clicks[0] == 0 && routed_clicks[1] == 1 && routed_clicks[2] == 0);
        std::fprintf(stderr, "Cross-process Section reader delivered %u click(s) to original sink\n", routed_clicks[1]);
    }
    CHECK(DestroyWindow(routes[0])); routes[0] = nullptr;
    click(20, 20, 0, "destroyed sink falls back to host");
    // Hardware pointer events select a composition sink before delegation.
    Handle ready, worker, pointer_sink;
    ready.value = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    DWORD worker_id = 0;
    worker.value = CreateThread(nullptr, 0, pointer_worker, ready.value, 0, &worker_id);
    CHECK(worker.value && WaitForSingleObject(ready.value, 5000) == WAIT_OBJECT_0 && pointer_destination);
    struct Stop { DWORD id; HANDLE thread; ~Stop() { if (thread) { PostThreadMessageW(id, WM_QUIT, 0, 0); WaitForSingleObject(thread, 5000); } } } stop{worker_id, worker.value};
    if (!worker.value || !pointer_destination) return;
    Descriptor desc = {}; desc.size = 0xe8;
    for (unsigned i=0;i<4;++i) { desc.routes[i].type=2; desc.routes[i].window=reinterpret_cast<UINT_PTR>(pointer_destination); }
    CHECK(create(&desc, &pointer_sink.value) == 0);
    HR(set(root.p, pointer_sink.value)); HR(producer->Commit()); HR(consumer->Commit());
    using Delegate = BOOL(WINAPI *)(DWORD, ULONG_PTR(WINAPI *)(const MSG *,void *),void *,HWND,UINT);
    using Undelegate = BOOL(WINAPI *)(HWND,UINT);
    using Hardware = ULONG_PTR(WINAPI *)(HWND,ULONG_PTR,DWORD);
    auto delegate = reinterpret_cast<Delegate>(reinterpret_cast<void *>(GetProcAddress(GetModuleHandleW(L"user32.dll"), "DelegateInput")));
    auto undelegate = reinterpret_cast<Undelegate>(reinterpret_cast<void *>(GetProcAddress(GetModuleHandleW(L"user32.dll"), "UndelegateInput")));
    auto hardware = reinterpret_cast<Hardware>(reinterpret_cast<void *>(GetProcAddress(GetModuleHandleW(L"win32u.dll"), "NtUserCallHwndParam")));
    CHECK(delegate && undelegate && hardware);
    if (!delegate || !undelegate || !hardware) return;
    CHECK(delegate(0, routed_delegate, nullptr, pointer_destination, 0x1000));
    POINT pt{20,20}; CHECK(ClientToScreen(window.handle,&pt)); CHECK(SetCursorPos(pt.x,pt.y)); pump();
    INPUT event = {}; event.type=INPUT_HARDWARE; event.hi.uMsg=WM_POINTERENTER; event.hi.wParamL=0x321;
    struct { UINT flags; const INPUT *input; LPARAM lparam; } params{0,&event,0};
    CHECK(hardware(window.handle,reinterpret_cast<ULONG_PTR>(&params),24) == 0);
    for (unsigned i=0;i<30 && !pointer_deliveries;++i) pump();
    CHECK(pointer_callbacks == 1 && pointer_deliveries == 1);
    std::fprintf(stderr,"Composition pointer delegation: callbacks=%ld forwarded=%ld\n",pointer_callbacks,pointer_deliveries);
    CHECK(undelegate(pointer_destination,2));
    HR(target->SetRoot(nullptr)); HR(consumer->Commit());
    click(20, 20, 0, "unbound scene");
    if (pointer_mouse_test)
    {
        CHECK(mouse_pointer_down > 0 && mouse_pointer_down == mouse_pointer_up);
        std::fprintf(stderr,"Composition mouse pointer: down=%u up=%u; cross-thread geometry checked\n",
                     mouse_pointer_down,mouse_pointer_up);
    }
}
