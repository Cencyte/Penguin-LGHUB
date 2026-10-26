// Embedded below-child targets, tested against final private KWin pixels.
#include "dcomp_window_surface.h"
#include <vector>
static DWORD WINAPI release_composition_target(void *value)
{
    static_cast<IUnknown *>(value)->Release();
    return 0;
}
static BOOL CALLBACK count_composition_children(HWND, LPARAM value)
{
    ++*reinterpret_cast<unsigned *>(value);
    return TRUE;
}
static unsigned composition_children(HWND window)
{
    unsigned count = 0;
    EnumChildWindows(window, count_composition_children, reinterpret_cast<LPARAM>(&count));
    return count;
}
static void child_target_run(HMODULE module)
{
    SharedApi api(module);
    Ref<ID3D11Device> d3d;
    Ref<ID3D11DeviceContext> context;
    Ref<IDXGIDevice> dxgi;
    Ref<IDCompositionDesktopDevice> device, second;
    Ref<IDCompositionSurface> surface;
    Ref<IDCompositionVisual2> visual;
    Ref<IDCompositionTarget> target, duplicate;
    Window window;
    CHECK(SetWindowPos(window.handle, nullptr, 100, 100, 240, 240, SWP_NOZORDER | SWP_NOACTIVATE));
    WNDCLASSW cls = {};
    cls.hInstance = GetModuleHandleW(nullptr);
    cls.lpfnWndProc = DefWindowProcW;
    cls.lpszClassName = L"GHUBChildBlack";
    cls.hbrBackground = static_cast<HBRUSH>(GetStockObject(BLACK_BRUSH));
    CHECK(RegisterClassW(&cls));
    HWND sibling = CreateWindowW(cls.lpszClassName, L"", WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS,
                                  60, 60, 80, 80, window.handle, nullptr, cls.hInstance, nullptr);
    CHECK(sibling);
    auto submit = reinterpret_cast<CompositionCall>(reinterpret_cast<void *>(
        GetProcAddress(GetModuleHandleW(L"win32u.dll"), "NtUserCallHwndParam")));
    CHECK(submit != nullptr);
    if (!submit) return;
    CompositionFrame frame{composition_version, composition_attach, 0x1234};
    auto send_frame = [&]() { return submit(window.handle, reinterpret_cast<ULONG_PTR>(&frame), composition_call); };
    frame.version = 0;
    CHECK(!send_frame());
    frame.version = composition_version;
    CHECK(send_frame());
    CHECK(!send_frame()); // duplicate reservation cannot steal the target
    frame.operation = composition_update;
    frame.width = frame.height = 1;
    CHECK(!send_frame()); // missing pixel buffer
    frame.width = 16385;
    CHECK(!send_frame()); // bounded size, checked before allocation
    frame.operation = composition_detach;
    frame.owner = 0x5678;
    CHECK(!send_frame()); // wrong owner cannot release another target
    frame.operation = composition_validate;
    frame.owner = 0x1234;
    CHECK(send_frame());
    frame.operation = composition_detach;
    CHECK(send_frame());
    CHECK(composition_children(window.handle) == 1);
    // Lazy promotion must preserve pixels painted before any retained target
    // existed. A white background would conceal a freshly initialized bitmap.
    {
        pump(); // finish the initial WM_ERASEBKGND before drawing the marker
        RECT painted = {0, 0, 40, 40};
        HDC dc = GetDC(window.handle);
        HBRUSH brush = CreateSolidBrush(RGB(20, 160, 70));
        CHECK(dc && brush);
        CHECK(FillRect(dc, &painted, brush));
        ReleaseDC(window.handle, dc);
        DeleteObject(brush);
        pixel(window.handle, 4, 4, 20, 160, 70);
        frame.operation = composition_attach;
        CHECK(send_frame());
        unsigned transparent = 0;
        frame.operation = composition_update;
        frame.width = frame.height = 1;
        frame.pixels = &transparent;
        CHECK(send_frame());
        pixel(window.handle, 4, 4, 20, 160, 70);
        CHECK(send_frame());
        pixel(window.handle, 4, 4, 20, 160, 70);
        frame.operation = composition_detach;
        CHECK(send_frame());
        pixel(window.handle, 4, 4, 20, 160, 70);
        RedrawWindow(window.handle, nullptr, nullptr, RDW_INVALIDATE | RDW_ERASE | RDW_UPDATENOW);
    }
    HR(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                         nullptr, 0, D3D11_SDK_VERSION, &d3d.p, nullptr, &context.p));
    HR(d3d->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void **>(&dxgi.p)));
    HR(api.device(dxgi.p, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&device.p)));
    HR(api.device(dxgi.p, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&second.p)));
    HR(device->CreateSurface(240, 240, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_ALPHA_MODE_PREMULTIPLIED, &surface.p));
    HR(device->CreateVisual(&visual.p));
    HR(visual->SetContent(surface.p));
    SetEnvironmentVariableW(L"GHUB_DCOMP_POPUP_TARGET", nullptr);
    SetEnvironmentVariableW(L"GHUB_DCOMP_CHILD_TARGET", L"1");
    HR(device->CreateTargetForHwnd(window.handle, FALSE, &target.p));
    CHECK(composition_children(window.handle) == 1);
    CHECK(GetWindow(window.handle, GW_CHILD) == sibling);
    CHECK(FAILED(device->CreateTargetForHwnd(window.handle, FALSE, &duplicate.p)) && !duplicate.p);
    CHECK(FAILED(second->CreateTargetForHwnd(window.handle, FALSE, &duplicate.p)) && !duplicate.p);
    CHECK(FAILED(device->CreateTargetForHwnd(window.handle, TRUE, &duplicate.p)) && !duplicate.p);
    HR(target->SetRoot(visual.p));
    HR(fill(surface.p, d3d.p, context.p, nullptr, .5f, 0, 0, .5f));
    pixel(window.handle, 4, 4, 255, 255, 255);
    HR(device->Commit());
    CHECK(composition_children(window.handle) == 1);
    pixel(window.handle, 4, 4, 255, 127, 127);
    pixel(window.handle, 184, 184, 255, 127, 127);
    pixel(window.handle, 64, 64, 0, 0, 0);
    POINT point = {80, 80};
    ClientToScreen(window.handle, &point);
    CHECK(WindowFromPoint(point) == sibling);
    point = {20, 20};
    ClientToScreen(window.handle, &point);
    CHECK(WindowFromPoint(point) == window.handle);
    for (unsigned i = 0; i < 3; ++i)
    {
        RedrawWindow(window.handle, nullptr, nullptr, RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW);
        pixel(window.handle, 4, 4, 255, 127, 127);
    }
    CHECK(SetWindowPos(window.handle, nullptr, 400, 100, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE));
    pixel(window.handle, 4, 4, 255, 127, 127); // follows movement without Commit
    ShowWindow(sibling, SW_HIDE);
    pixel(window.handle, 64, 64, 255, 127, 127);
    ShowWindow(sibling, SW_SHOW);
    pixel(window.handle, 64, 64, 0, 0, 0);
    // A child created after composition remains above the below-child target.
    HWND newer = CreateWindowW(cls.lpszClassName, L"", WS_CHILD | WS_VISIBLE | WS_CLIPSIBLINGS,
                                160, 160, 60, 60, window.handle, nullptr, cls.hInstance, nullptr);
    CHECK(newer);
    pixel(window.handle, 164, 164, 0, 0, 0);
    CHECK(DestroyWindow(newer));
    pixel(window.handle, 164, 164, 255, 127, 127);
    HR(target->SetRoot(nullptr));
    HR(device->Commit());
    pixel(window.handle, 4, 4, 255, 255, 255);
    target.reset();
    HR(device->CreateTargetForHwnd(window.handle, FALSE, &target.p));
    target.reset();

    // Match the real DesktopChildSiteBridge: hidden WS_CHILD, initially 0x0.
    HWND bridge = CreateWindowExW(WS_EX_NOREDIRECTIONBITMAP, L"GHUBDCompPixelTest", L"",
                                   WS_CHILD | WS_CLIPSIBLINGS, 20, 20, 0, 0,
                                   window.handle, nullptr, cls.hInstance, nullptr);
    CHECK(bridge);
    HR(device->CreateTargetForHwnd(bridge, FALSE, &target.p));
    CHECK(composition_children(bridge) == 0);
    CHECK(GetWindow(bridge, GW_CHILD) == nullptr);
    CHECK(GetParent(bridge) == window.handle);
    HR(target->SetRoot(visual.p));
    HR(device->Commit()); // zero-size has no pixels; creation still succeeds
    CHECK(SetWindowPos(bridge, HWND_TOP, 20, 20, 100, 100, SWP_NOACTIVATE | SWP_SHOWWINDOW));
    HR(device->Commit());
    CHECK(composition_children(bridge) == 0);
    pixel(window.handle, 14, 14, 255, 127, 127);
    pixel(window.handle, 114, 14, 255, 255, 255); // parent clipping
    CHECK(SetWindowPos(bridge, nullptr, 20, 20, 180, 100, SWP_NOACTIVATE | SWP_NOZORDER));
    HR(device->Commit());
    pixel(window.handle, 154, 14, 255, 127, 127); // backing buffer grows across 128px alignment
    ShowWindow(bridge, SW_HIDE);
    pixel(window.handle, 14, 14, 255, 255, 255); // hidden ancestor without Commit
    HR(fill(surface.p, d3d.p, context.p, nullptr, 0, 0, .5f, .5f));
    HR(device->Commit()); // retain a frame committed while its ancestor is hidden
    ShowWindow(bridge, SW_SHOW);
    pixel(window.handle, 14, 14, 127, 127, 255);
    CHECK(SetWindowPos(bridge, nullptr, 200, 20, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOZORDER));
    pixel(window.handle, 14, 14, 255, 255, 255);
    pixel(window.handle, 204, 14, 127, 127, 255); // child movement without Commit
    CHECK(SetWindowPos(bridge, nullptr, 20, 20, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOZORDER));
    {
        Window other;
        CHECK(SetWindowPos(other.handle, nullptr, 100, 400, 240, 240, SWP_NOACTIVATE | SWP_NOZORDER));
        CHECK(SetParent(bridge, other.handle) == window.handle);
        CHECK(SetWindowPos(bridge, nullptr, 20, 20, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOZORDER));
        pixel(window.handle, 14, 14, 255, 255, 255);
        pixel(other.handle, 14, 14, 127, 127, 255); // both roots update after reparenting
        CHECK(SetParent(bridge, window.handle) == other.handle);
        CHECK(SetWindowPos(bridge, nullptr, 20, 20, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOZORDER));
        pixel(other.handle, 14, 14, 255, 255, 255);
        pixel(window.handle, 14, 14, 127, 127, 255);
    }
    // Releasing a live target removes its frame without destroying the HWND.
    HANDLE release_thread = CreateThread(nullptr, 0, release_composition_target, target.p, 0, nullptr);
    CHECK(release_thread != nullptr);
    if (release_thread)
    {
        target.p = nullptr;
        CHECK(WaitForSingleObject(release_thread, 10000) == WAIT_OBJECT_0);
        CloseHandle(release_thread);
    }
    else target.reset();
    CHECK(IsWindow(bridge));
    CHECK(composition_children(bridge) == 0);
    pixel(window.handle, 14, 14, 255, 255, 255);
    HR(device->CreateTargetForHwnd(bridge, FALSE, &target.p));
    HR(target->SetRoot(visual.p));
    HR(device->Commit());
    pixel(window.handle, 14, 14, 127, 127, 255);
    CHECK(DestroyWindow(bridge));
    CHECK(FAILED(device->Commit()));
    target.reset();
    pixel(window.handle, 14, 14, 255, 255, 255);
    // A no-redirection host has no GDI base. Its transparent margin must reveal
    // subsequent background paints, without another composition commit.
    HWND back = CreateWindowW(cls.lpszClassName,L"changing background",WS_POPUP|WS_VISIBLE,
        600,100,300,260,nullptr,nullptr,cls.hInstance,nullptr);
    HWND clear = CreateWindowExW(WS_EX_NOREDIRECTIONBITMAP,L"GHUBDCompPixelTest",L"transparent host",
        WS_POPUP|WS_VISIBLE,620,120,180,180,nullptr,nullptr,cls.hInstance,nullptr);
    CHECK(back && clear);
    CompositionFrame transparent_frame{composition_version,composition_attach,0x9876};
    auto submit_clear = [&]() { return submit(clear,reinterpret_cast<ULONG_PTR>(&transparent_frame),composition_call); };
    CHECK(submit_clear());
    std::vector<unsigned> clear_pixels(180*180,0);
    for (unsigned y=0;y<180;++y) for (unsigned x=0;x<60;++x) clear_pixels[y*180+x]=0x80800000;
    transparent_frame.operation=composition_update;
    transparent_frame.width=transparent_frame.height=180;
    transparent_frame.pixels=clear_pixels.data();
    CHECK(submit_clear());
    pixel(clear,10,10,128,0,0);
    pixel(clear,110,10,0,0,0);
    HBRUSH green = CreateSolidBrush(RGB(0,128,0));
    auto old_brush = SetClassLongPtrW(back,GCLP_HBRBACKGROUND,reinterpret_cast<LONG_PTR>(green));
    RedrawWindow(back,nullptr,nullptr,RDW_INVALIDATE|RDW_ERASE|RDW_UPDATENOW);
    pixel(clear,10,10,128,64,0);
    pixel(clear,110,10,0,128,0);
    // No-redirection is rectangular for hit testing, even over alpha-zero pixels.
    POINT clear_point{130,30}; CHECK(ClientToScreen(clear,&clear_point));
    CHECK(WindowFromPoint(clear_point)==clear);
    CHECK(SetWindowPos(clear,nullptr,660,140,0,0,SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE));
    pixel(clear,110,10,0,128,0);
    transparent_frame.operation=composition_detach; CHECK(submit_clear());
    pixel(clear,10,10,0,128,0);
    DestroyWindow(clear); DestroyWindow(back);
    SetClassLongPtrW(sibling,GCLP_HBRBACKGROUND,old_brush); DeleteObject(green);
    DestroyWindow(sibling);
    UnregisterClassW(cls.lpszClassName, cls.hInstance);
    SetEnvironmentVariableW(L"GHUB_DCOMP_CHILD_TARGET", nullptr);
}
