/* Real window-pixel regression for the isolated DirectComposition prototype.
 * Must run on Xvfb: this executable creates and samples its own test window.
 * Public contract: https://learn.microsoft.com/windows/win32/api/dcomp/nf-dcomp-idcompositionsurface-begindraw
 */
#include <windows.h>
#include <winternl.h>
#include <dcomp.h>
#include <d3d11.h>
#include <d2d1_1.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>

static unsigned failures;
#define CHECK(x)                                                                                                       \
    do                                                                                                                 \
    {                                                                                                                  \
        if (!(x))                                                                                                      \
        {                                                                                                              \
            std::fprintf(stderr, "FAIL line %u: %s\n", __LINE__, #x);                                                  \
            ++failures;                                                                                                \
        }                                                                                                              \
    } while (0)
#define HR(x)                                                                                                          \
    do                                                                                                                 \
    {                                                                                                                  \
        HRESULT result = (x);                                                                                          \
        if (FAILED(result))                                                                                            \
        {                                                                                                              \
            std::fprintf(stderr, "FAIL line %u: %s = %08lx\n", __LINE__, #x, static_cast<unsigned long>(result));      \
            ++failures;                                                                                                \
            return;                                                                                                    \
        }                                                                                                              \
    } while (0)
template <class T> struct Ref
{
    T *p = nullptr;
    ~Ref()
    {
        if (p)
            p->Release();
    }
    T *operator->() const
    {
        return p;
    }
    void reset()
    {
        if (p)
            p->Release();
        p = nullptr;
    }
};
struct Window
{
    HWND handle;
    Window()
    {
        WNDCLASSW cls = {};
        cls.hInstance = GetModuleHandleW(nullptr);
        cls.lpfnWndProc = DefWindowProcW;
        cls.lpszClassName = L"GHUBDCompPixelTest";
        cls.hbrBackground = static_cast<HBRUSH>(GetStockObject(WHITE_BRUSH));
        RegisterClassW(&cls);
        handle = CreateWindowExW(WS_EX_NOACTIVATE, cls.lpszClassName, L"Isolated composition test",
                                 WS_POPUP | WS_VISIBLE, 40, 40, 64, 64, nullptr, nullptr, cls.hInstance, nullptr);
    }
    ~Window()
    {
        if (IsWindow(handle))
            DestroyWindow(handle);
        UnregisterClassW(L"GHUBDCompPixelTest", GetModuleHandleW(nullptr));
    }
};
static void pump()
{
    for (unsigned i = 0; i < 10; ++i)
    {
        MSG msg;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE))
        {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        Sleep(10);
    }
}
static void pixel(HWND window, int x, int y, int red, int green, int blue)
{
    // Keep samples inside the client area, away from window-manager corner
    // rounding and shadows, which are not part of the composition graph.
    pump();
    POINT pos = {x + 16, y + 16};
    ClientToScreen(window, &pos);
    HDC dc = GetDC(nullptr);
    COLORREF value = GetPixel(dc, pos.x, pos.y);
    ReleaseDC(nullptr, dc);
    bool ok = value != CLR_INVALID && std::abs(static_cast<int>(GetRValue(value)) - red) <= 1 &&
              std::abs(static_cast<int>(GetGValue(value)) - green) <= 1 &&
              std::abs(static_cast<int>(GetBValue(value)) - blue) <= 1;
    bool external = GetEnvironmentVariableW(L"GHUB_DCOMP_EXTERNAL_CAPTURE", nullptr, 0) != 0;
    std::fprintf(stderr, "pixel(%d,%d) RGB=%u,%u,%u expected=%d,%d,%d %s\n", x, y, GetRValue(value), GetGValue(value),
                 GetBValue(value), red, green, blue,
                 external ? "GDI diagnostic"
                 : ok     ? "PASS"
                          : "FAIL");
    if (external)
    {
        std::fprintf(stderr, "CAPTURE %ld %ld %d %d %d\n", pos.x, pos.y, red, green, blue);
        CHECK(std::getchar() == '\n'); // private harness acknowledges its X-server sample
    }
    else
        CHECK(ok);
}
static HRESULT fill(IDCompositionSurface *surface, ID3D11Device *d3d, ID3D11DeviceContext *context, const RECT *rect,
                    float red, float green, float blue, float alpha, bool end = true)
{
    Ref<IDXGISurface> dxgi;
    Ref<ID3D11Texture2D> texture;
    Ref<ID3D11RenderTargetView> view;
    POINT offset;
    HRESULT hr = surface->BeginDraw(rect, __uuidof(IDXGISurface), reinterpret_cast<void **>(&dxgi.p), &offset);
    if (FAILED(hr))
        return hr;
    CHECK(offset.x == 0 && offset.y == 0);
    hr = dxgi->QueryInterface(__uuidof(ID3D11Texture2D), reinterpret_cast<void **>(&texture.p));
    if (SUCCEEDED(hr))
        hr = d3d->CreateRenderTargetView(texture.p, nullptr, &view.p);
    if (SUCCEEDED(hr))
    {
        const float color[] = {red, green, blue, alpha};
        context->ClearRenderTargetView(view.p, color);
    }
    if (end || FAILED(hr))
    {
        HRESULT result = surface->EndDraw();
        if (SUCCEEDED(hr))
            hr = result;
    }
    return hr;
}

static DWORD WINAPI commit_thread(void *argument)
{
    return static_cast<IDCompositionDesktopDevice *>(argument)->Commit();
}

static void run(HMODULE module, bool native_d2d, bool legacy)
{
    using Create = HRESULT(WINAPI *)(IUnknown *, REFIID, void **);
    auto create =
        reinterpret_cast<Create>(reinterpret_cast<void *>(GetProcAddress(module, "DCompositionCreateDevice2")));
    CHECK(create);
    if (!create)
        return;
    Ref<ID3D11Device> d3d;
    Ref<ID3D11DeviceContext> context;
    Ref<IDXGIDevice> dxgi;
    Ref<IDCompositionDesktopDevice> device;
    Ref<IDCompositionDevice> device1;
    Ref<IUnknown> identity, identity1;
    Ref<IDCompositionSurface> surface, alpha_surface;
    Ref<IDCompositionVisual2> root, child;
    Window window;
    CHECK(window.handle);
    if (!window.handle)
        return;
    pump(); // let the private window manager settle the owner's initial position
    Ref<IDCompositionTarget> target;
    HR(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0,
                         D3D11_SDK_VERSION, &d3d.p, nullptr, &context.p));
    HR(d3d->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void **>(&dxgi.p)));
    if (legacy)
    {
        using Create1 = HRESULT(WINAPI *)(IDXGIDevice *, REFIID, void **);
        auto create1 =
            reinterpret_cast<Create1>(reinterpret_cast<void *>(GetProcAddress(module, "DCompositionCreateDevice")));
        CHECK(create1);
        if (!create1)
            return;
        HR(create1(dxgi.p, __uuidof(IDCompositionDevice), reinterpret_cast<void **>(&device1.p)));
        HR(device1->QueryInterface(__uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&device.p)));
    }
    else
    {
        HR(create(dxgi.p, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&device.p)));
        HR(device->QueryInterface(__uuidof(IDCompositionDevice), reinterpret_cast<void **>(&device1.p)));
    }
    HR(device->QueryInterface(__uuidof(IUnknown), reinterpret_cast<void **>(&identity.p)));
    HR(device1->QueryInterface(__uuidof(IUnknown), reinterpret_cast<void **>(&identity1.p)));
    CHECK(identity.p == identity1.p);
    identity.reset();
    identity1.reset();
    BOOL valid = FALSE;
    HR(device1->CheckDeviceState(&valid));
    CHECK(valid);
    HR(device1->CreateSurface(4, 4, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_ALPHA_MODE_PREMULTIPLIED, &surface.p));
    HR(device->CreateSurface(2, 2, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_ALPHA_MODE_PREMULTIPLIED, &alpha_surface.p));
    Ref<IDCompositionVisual> visual1;
    HR(device1->CreateVisual(&visual1.p));
    HR(visual1->QueryInterface(__uuidof(IDCompositionVisual2), reinterpret_cast<void **>(&root.p)));
    visual1.reset();
    HR(root->SetContent(surface.p));
    HR(root->SetOffsetX(16.0f));
    HR(root->SetOffsetY(16.0f));
    HR(device->CreateVisual(&child.p));
    HR(child->SetContent(alpha_surface.p));
    SetEnvironmentVariableW(L"GHUB_DCOMP_POPUP_TARGET", nullptr);
    CHECK(FAILED(device->CreateTargetForHwnd(window.handle, TRUE, &target.p)) && !target.p);
    SetEnvironmentVariableW(L"GHUB_DCOMP_POPUP_TARGET", L"1");
    HR(device1->CreateTargetForHwnd(window.handle, TRUE, &target.p));
    HR(target->SetRoot(root.p));
    HR(device1->Commit());
    HR(device1->WaitForCommitCompletion());
    pixel(window.handle, 1, 1, 255, 255, 255); // uninitialized surfaces are transparent
    RECT partial = {1, 1, 3, 3};
    POINT offset;
    Ref<IDXGISurface> invalid;
    CHECK(FAILED(surface->BeginDraw(&partial, __uuidof(IDXGISurface), reinterpret_cast<void **>(&invalid.p), &offset)));
    CHECK(!invalid.p);
    HR(fill(surface.p, d3d.p, context.p, nullptr, 1, 0, 0, 1));
    pixel(window.handle, 1, 1, 255, 255, 255); // EndDraw alone must not publish
    HR(device->Commit());
    pixel(window.handle, 1, 1, 255, 0, 0);
    pixel(window.handle, 8, 8, 255, 255, 255);
    HR(fill(surface.p, d3d.p, context.p, nullptr, 0, 0, 1, 1, false));
    CHECK(FAILED(
        alpha_surface->BeginDraw(nullptr, __uuidof(IDXGISurface), reinterpret_cast<void **>(&invalid.p), &offset)));
    CHECK(!invalid.p); // only one active update per device
    HR(device->Commit());
    pixel(window.handle, 1, 1, 255, 0, 0); // pending drawing remains pending
    HR(surface->EndDraw());
    CHECK(FAILED(surface->EndDraw()));
    pixel(window.handle, 1, 1, 255, 0, 0);
    HR(device->Commit());
    pixel(window.handle, 1, 1, 0, 0, 255);
    HR(fill(surface.p, d3d.p, context.p, &partial, 0, 1, 0, 1));
    HR(device->Commit());
    pixel(window.handle, 1, 1, 0, 255, 0);
    pixel(window.handle, 0, 0, 0, 0, 255);
    HR(fill(alpha_surface.p, d3d.p, context.p, nullptr, 0.5f, 0, 0, 0.5f));
    HR(child->SetOffsetX(1.0f));
    HR(child->SetOffsetY(1.0f));
    HR(root->AddVisual(child.p, TRUE, nullptr));
    CHECK(FAILED(child->AddVisual(root.p, TRUE, nullptr))); // no reference cycles
    D2D_RECT_F clip = {0, 0, 1, 2};
    HR(child->SetClip(clip));
    HR(device->Commit());
    pixel(window.handle, 1, 1, 128, 127, 0);
    pixel(window.handle, 2, 1, 0, 255, 0);
    HR(root->RemoveVisual(child.p));
    pixel(window.handle, 1, 1, 128, 127, 0);
    HR(device->Commit());
    pixel(window.handle, 1, 1, 0, 255, 0);
    HR(root->SetOffsetX(21.0f));
    HR(device->Commit());
    pixel(window.handle, 1, 1, 255, 255, 255);
    pixel(window.handle, 6, 1, 0, 255, 0);
    CHECK(root->SetOffsetX(std::numeric_limits<float>::infinity()) == E_INVALIDARG); // prior transform retained
    HR(device->Commit());
    pixel(window.handle, 6, 1, 0, 255, 0);
    CHECK(SetWindowPos(window.handle, nullptr, 130, 120, 128, 112, SWP_NOACTIVATE | SWP_NOZORDER));
    pump();
    HR(device->Commit());
    pixel(window.handle, 6, 1, 0, 255, 0);
    pixel(window.handle, 90, 70, 255, 255, 255);
    HANDLE thread = CreateThread(nullptr, 0, commit_thread, device.p, 0, nullptr);
    CHECK(thread);
    if (thread)
    {
        CHECK(WaitForSingleObject(thread, 5000) == WAIT_OBJECT_0);
        DWORD result = 0;
        CHECK(GetExitCodeThread(thread, &result));
        CHECK(result == static_cast<DWORD>(RPC_E_WRONG_THREAD));
        CloseHandle(thread);
    }
    if (native_d2d)
    {
        Ref<ID2D1Factory1> factory;
        Ref<ID2D1Device> d2d;
        Ref<IDCompositionSurfaceFactory> surfaces;
        Ref<IDCompositionSurface> d2d_surface;
        Ref<ID2D1DeviceContext> draw;
        D2D1_FACTORY_OPTIONS options = {};
        HR(D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, __uuidof(ID2D1Factory1), &options,
                             reinterpret_cast<void **>(&factory.p)));
        HR(factory->CreateDevice(dxgi.p, &d2d.p));
        HR(device->CreateSurfaceFactory(d2d.p, &surfaces.p));
        HR(surfaces->CreateSurface(4, 4, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_ALPHA_MODE_PREMULTIPLIED, &d2d_surface.p));
        HR(d2d_surface->BeginDraw(nullptr, __uuidof(ID2D1DeviceContext), reinterpret_cast<void **>(&draw.p), &offset));
        D2D1_COLOR_F yellow = {1, 1, 0, 1};
        draw->Clear(&yellow);
        HR(d2d_surface->EndDraw());
        draw.reset();
        HR(root->SetContent(d2d_surface.p));
        HR(device->Commit());
        pixel(window.handle, 6, 1, 255, 255, 0);
        // The composition surface retains its D2D device after its factory dies.
        surfaces.reset();
        d2d.reset();
        factory.reset();
        HR(d2d_surface->BeginDraw(&partial, __uuidof(ID2D1DeviceContext), reinterpret_cast<void **>(&draw.p), &offset));
        D2D1_COLOR_F magenta = {1, 0, 1, 1};
        draw->Clear(&magenta);
        HR(d2d_surface->EndDraw());
        draw.reset();
        pixel(window.handle, 6, 1, 255, 255, 0);
        HR(device->Commit());
        pixel(window.handle, 6, 1, 255, 0, 255);
        pixel(window.handle, 5, 0, 255, 255, 0);
    }
    HR(target->SetRoot(nullptr));
    HR(device->Commit());
    pixel(window.handle, 6, 1, 255, 255, 255);
    HR(root->SetContent(surface.p));
    HR(target->SetRoot(root.p));
    HR(device->Commit());
    pixel(window.handle, 6, 1, 0, 255, 0);
    CHECK(DestroyWindow(window.handle));
    CHECK(FAILED(device->Commit()));
    // Drop the public device pointer first: children must keep their owner alive.
    device.reset();
    device1.reset();
    target.reset();
    root.reset();
    child.reset();
    surface.reset();
    alpha_surface.reset();
}
#include "dcomp_shared_test.h"
#include "dcomp_layout_test.h"
#include "dcomp_input_test.h"
#include "dcomp_input_routing_test.h"
#include "dcomp_sampling_test.h"
#include "dcomp_formats_test.h"
#include "dcomp_resize_test.h"
#include "dcomp_child_test.h"

int main(int argc, char **argv)
{
    bool native_d2d = argc == 2 && !std::strcmp(argv[1], "--native-d2d");
    bool embedded = argc == 2 && !std::strcmp(argv[1], "--embedded-child");
    bool child = argc == 3 && !std::strcmp(argv[1], "--shared-child");
    bool input_child = argc == 3 && !std::strcmp(argv[1], "--input-child");
    bool input = argc == 2 && !std::strcmp(argv[1], "--input-binding");
    if (argc > 1 && !native_d2d && !child && !embedded && !input && !input_child)
        return 2;
    std::setbuf(stderr, nullptr);
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    HMODULE module = LoadLibraryW(L"dcomp.dll");
    if (!module)
    {
        std::fprintf(stderr, "Load dcomp: %lu\n", GetLastError());
        return 2;
    }
    if (input_child)
        input_routing_child(module, reinterpret_cast<HANDLE>(static_cast<ULONG_PTR>(std::strtoull(argv[2], nullptr, 10))));
    else if (child)
        shared_child(module, reinterpret_cast<HANDLE>(static_cast<ULONG_PTR>(std::strtoull(argv[2], nullptr, 10))));
    else if (embedded)
        child_target_run(module);
    else if (input)
    {
        input_routing_run(module);
        input_binding_run(module);
        input_publication_pixels(module);
    }
    else
    {
        run(module, native_d2d, false);
        run(module, native_d2d, true);
        shared_run(module);
        shared_refresh_run(module);
        shared_endpoint_lifetime_run(module);
        layout_run(module);
        sampling_run(module);
        formats_run(module, native_d2d);
        resize_run(module);
    }
    using Unload = HRESULT(WINAPI *)();
    auto unload = reinterpret_cast<Unload>(reinterpret_cast<void *>(GetProcAddress(module, "DllCanUnloadNow")));
    CHECK(unload && unload() == S_OK);
    FreeLibrary(module);
    CoUninitialize();
    std::fprintf(stderr, "DirectComposition window pixels/lifetime: %s (%u failures)\n", failures ? "FAIL" : "PASS",
                 failures);
    return !!failures;
}
