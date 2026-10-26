/* Experimental user-mode DirectComposition backend for Wine, x86-64 only.
 * No installed Wine files are replaced. Implemented scope: BGRA8/RGBA8 surfaces,
 * fractional translations, inherited nearest/linear sampling and soft/hard edges,
 * relative size/offset layout, rectangular clipping, source-over visual trees,
 * opt-in same-thread below-child targets with the Win32u lab patch,
 * diagnostic owned-popup targets, synchronous batch submission,
 * shared visual/target handles with committed cross-process BGRA frames.
 * Private SetInput retains validated sink identities; input-bound publication
 * and routing require the matched server/Win32u input-scene implementation.
 * Same-process shared consumers are refreshed after publication, dispatched
 * back to the consumer device's owning thread when its apartment differs.
 * Cross-process consumers still require an explicit Commit after handoff.
 * Bundled FrameworkUdk's pre-contract-12 logical-surface resize discards
 * pending content and requires a full redraw; virtual surfaces are unsupported.
 * Effects, animation, shared surfaces, swapchains and other private methods
 * are explicitly unsupported. This is not a complete WinUI compositor.
 */
#include <windows.h>
#include <winternl.h>
#include <dcomp.h>
#include <d3d11.h>
#include <d2d1_3.h>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <new>
#include <vector>
#include <utility>
#include "dcomp_window_surface.h"
#include "dcomp_raster.h"

static_assert(sizeof(void *) == 8, "Private ABI probe supports x86-64 only");
static const IID partner_rs5 = {0x25682ec4, 0x73a4, 0x4022, {0xa0, 0x4f, 0x1b, 0xbe, 0x6d, 0xc3, 0xe1, 0xf0}};
static const IID partner_vb = {0x28d6ad3d, 0xee2a, 0x4bcd, {0x94, 0x19, 0x7d, 0x54, 0x80, 0x04, 0x35, 0xb1}};
static const IID restricted = {0x6b556968, 0x2d25, 0x11e2, {0x99, 0xf6, 0x8b, 0xc3, 0x60, 0x88, 0x70, 0x9b}};
// Older MinGW dcomp.h lacks __CRT_UUID_DECL for these three interfaces.
static const IID surface_iid = {0xbb8a4953, 0x2c99, 0x4f5a, {0x96, 0xf5, 0x48, 0x19, 0x02, 0x7f, 0xa3, 0xac}};
static const IID surface_partner_iid = {0x5680c979, 0x14c8, 0x48b5, {0x8b, 0x92, 0x32, 0x47, 0x37, 0x9f, 0xd9, 0x07}};
static const IID visual_iid = {0x4d93059d, 0x097b, 0x4651, {0x9a, 0x60, 0xf0, 0xf2, 0x51, 0x16, 0xe2, 0xf3}};
static const IID target_iid = {0xeacdd04c, 0x117e, 0x4e17, {0x88, 0xf4, 0xd1, 0xb1, 0x2b, 0x0e, 0x3d, 0x89}};
static const IID device1_iid = {0xc37ea93a, 0xe7aa, 0x450d, {0xb1, 0x6f, 0x97, 0x46, 0xcb, 0x04, 0x07, 0xf3}};
static const IID desktop_partner = {0xd14b6158, 0xc3fa, 0x4bce, {0x9c, 0x1f, 0xb6, 0x1d, 0x86, 0x65, 0xea, 0xb0}};
static const IID visual_restricted = {0x2c4eef28, 0x1bc0, 0x4736, {0xb7, 0xdd, 0xb6, 0x26, 0x92, 0xf9, 0xbd, 0x67}};
// Internal ownership interface for this backend; never advertised as a native ABI.
static const IID visual_owner_iid = {0x6894e408, 0x1e68, 0x4d2b, {0x84, 0x6c, 0xa9, 0xa3, 0x2b, 0x92, 0x1f, 0x83}};
static LONG objects;

template <class T> struct Ref
{
    T *p = nullptr;
    Ref() = default;
    ~Ref()
    {
        reset();
    }
    Ref(const Ref &) = delete;
    Ref &operator=(const Ref &) = delete;
    Ref(Ref &&other) noexcept : p(other.detach()) {}
    Ref &operator=(Ref &&other) noexcept { reset(other.detach()); return *this; }
    T *operator->() const
    {
        return p;
    }
    void reset(T *value = nullptr)
    {
        T *old = p;
        p = value;
        if (old)
            old->Release();
    }
    void retain(T *value)
    {
        if (value)
            value->AddRef();
        reset(value);
    }
    T *detach()
    {
        T *value = p;
        p = nullptr;
        return value;
    }
};
struct Lock
{
    CRITICAL_SECTION *cs;
    explicit Lock(CRITICAL_SECTION &value) : cs(&value)
    {
        EnterCriticalSection(cs);
    }
    ~Lock()
    {
        LeaveCriticalSection(cs);
    }
};
template <class T> static void erase(std::vector<T *> &list, T *value)
{
    list.erase(std::remove(list.begin(), list.end(), value), list.end());
}
static HRESULT unsupported(const char *method)
{
    std::fprintf(stderr, "dcomp_experiment: %s unsupported\n", method);
    return E_NOTIMPL;
}
template <class T> static HRESULT unsupported_out(T **out, const char *method)
{
    if (!out)
        return E_POINTER;
    *out = nullptr;
    return unsupported(method);
}
static HRESULT winerror()
{
    DWORD code = GetLastError();
    return HRESULT_FROM_WIN32(code ? code : ERROR_GEN_FAILURE);
}

#include "dcomp_input_binding.h"
#include "dcomp_input_scene.h"
#include "dcomp_shared_resource.h"

class Device;
class Surface;
class Visual;
class Target;
class SharedTarget;
class SharedResource;
void dcomp_shared_resource_published(SharedResource *resource, Device *publisher);
static const WCHAR shared_refresh_class[] = L"GHUBDCompSharedRefresh";
static constexpr UINT shared_refresh_message = WM_APP + 0x4d2;
struct VisualOwner : IUnknown
{
    virtual HRESULT STDMETHODCALLTYPE GetOwner(Device **out) = 0;
};
struct PrivateInterface
{
    void **vtable;
    Device *owner;
};
struct VisualLayoutInterface
{
    void **vtable;
    Visual *owner;
};
static void *visual_layout_table[52];
struct SurfaceInterface
{
    void **vtable;
    Surface *owner;
};
static void *surface_partner_table[16];
struct LayoutSize
{
    float width = 0, height = 0;
};

struct Renderer
{
    Ref<ID3D11Device> d3d;
    Ref<ID3D11DeviceContext> context;
    Ref<ID2D1Device> d2d;
    HRESULT initialize(IUnknown *input)
    {
        if (!input)
            return S_OK;
        Ref<IDXGIDevice> dxgi;
        HRESULT hr = input->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void **>(&dxgi.p));
        if (FAILED(hr))
        {
            Ref<ID2D1Device2> d2d2;
            hr = input->QueryInterface(__uuidof(ID2D1Device2), reinterpret_cast<void **>(&d2d2.p));
            if (FAILED(hr))
                return unsupported("rendering device type (requires DXGI or D2D Device2)");
            hr = d2d2->GetDxgiDevice(&dxgi.p);
            if (FAILED(hr))
                return hr;
            d2d.retain(d2d2.p);
        }
        hr = dxgi->QueryInterface(__uuidof(ID3D11Device), reinterpret_cast<void **>(&d3d.p));
        if (SUCCEEDED(hr))
            d3d->GetImmediateContext(&context.p);
        return hr;
    }
};

// Device1 and Device2 have different public method ordering. C++ provides a
// separate vtable/this-adjusting thunks; all QI paths share the Desktop identity.
class Device final : public IDCompositionDesktopDevice, public IDCompositionDevice
{
    LONG refs = 1;

  public:
    CRITICAL_SECTION mutex;
    Renderer renderer;
    std::vector<Surface *> surfaces;
    std::vector<Visual *> visuals;
    std::vector<Target *> targets;
    std::vector<SharedTarget *> shared_targets;
    Surface *drawing = nullptr;
    DWORD submitted = 0, confirmed = 0;
    HANDLE completion = nullptr;
    PrivateInterface rs5, vb, events, shared;
    static std::vector<Device *> registry;
    static CRITICAL_SECTION registry_mutex;
    static INIT_ONCE registry_once;
    static BOOL CALLBACK initialize_registry(PINIT_ONCE, PVOID, PVOID *);
    static void register_device(Device *device);
    static INIT_ONCE refresh_class_once;
    static BOOL CALLBACK initialize_refresh_class(PINIT_ONCE, PVOID, PVOID *);
    static LRESULT CALLBACK refresh_window_proc(HWND, UINT, WPARAM, LPARAM);
    DWORD owner_thread;
    HWND refresh_window = nullptr;
    bool refresh_queued = false; // registry_mutex, never the device lock
    HRESULT refresh_initialization = S_OK;
    bool has_imported(SharedResource *resource);
    HRESULT create_refresh_window();
    void dispatch_published_refresh();
    HRESULT submit(bool refresh);
    Device();
    ~Device();
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void **out) override;
    ULONG STDMETHODCALLTYPE AddRef() override
    {
        return InterlockedIncrement(&refs);
    }
    ULONG STDMETHODCALLTYPE Release() override
    {
        // Publication/window callbacks resolve weak registry entries under
        // this same lock. They cannot resurrect a device at reference zero.
        EnterCriticalSection(&registry_mutex);
        ULONG value = InterlockedDecrement(&refs);
        if (!value) erase(registry, this);
        LeaveCriticalSection(&registry_mutex);
        if (!value)
            delete this;
        return value;
    }
    HRESULT STDMETHODCALLTYPE Commit() override;
    HRESULT STDMETHODCALLTYPE WaitForCommitCompletion() override
    {
        Lock lock(mutex);
        return submitted == confirmed ? S_OK : E_PENDING;
    }
    HRESULT STDMETHODCALLTYPE GetFrameStatistics(DCOMPOSITION_FRAME_STATISTICS *out) override
    {
        if (!out)
            return E_POINTER;
        *out = {};
        return unsupported("GetFrameStatistics");
    }
    HRESULT STDMETHODCALLTYPE CreateVisual(IDCompositionVisual2 **out) override;
    HRESULT STDMETHODCALLTYPE CreateVisual(IDCompositionVisual **out) override;
    HRESULT STDMETHODCALLTYPE CheckDeviceState(BOOL *valid) override
    {
        if (!valid)
            return E_POINTER;
        Lock lock(mutex);
        *valid = !renderer.d3d.p || SUCCEEDED(renderer.d3d->GetDeviceRemovedReason());
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE CreateSurfaceFactory(IUnknown *input, IDCompositionSurfaceFactory **out) override;
    HRESULT STDMETHODCALLTYPE CreateSurface(UINT w, UINT h, DXGI_FORMAT format, DXGI_ALPHA_MODE alpha,
                                            IDCompositionSurface **out) override;
    HRESULT STDMETHODCALLTYPE CreateVirtualSurface(UINT, UINT, DXGI_FORMAT, DXGI_ALPHA_MODE,
                                                   IDCompositionVirtualSurface **out) override
    {
        return unsupported_out(out, "CreateVirtualSurface");
    }
#define OBJECT_STUB(name, type)                                                                                        \
    HRESULT STDMETHODCALLTYPE name(type **out) override                                                                \
    {                                                                                                                  \
        return unsupported_out(out, #name);                                                                            \
    }
    OBJECT_STUB(CreateTranslateTransform, IDCompositionTranslateTransform)
    OBJECT_STUB(CreateScaleTransform, IDCompositionScaleTransform)
    OBJECT_STUB(CreateRotateTransform, IDCompositionRotateTransform)
    OBJECT_STUB(CreateSkewTransform, IDCompositionSkewTransform)
    OBJECT_STUB(CreateMatrixTransform, IDCompositionMatrixTransform)
    OBJECT_STUB(CreateTranslateTransform3D, IDCompositionTranslateTransform3D)
    OBJECT_STUB(CreateScaleTransform3D, IDCompositionScaleTransform3D)
    OBJECT_STUB(CreateRotateTransform3D, IDCompositionRotateTransform3D)
    OBJECT_STUB(CreateMatrixTransform3D, IDCompositionMatrixTransform3D)
    OBJECT_STUB(CreateEffectGroup, IDCompositionEffectGroup)
    OBJECT_STUB(CreateRectangleClip, IDCompositionRectangleClip)
    OBJECT_STUB(CreateAnimation, IDCompositionAnimation)
#undef OBJECT_STUB
    HRESULT STDMETHODCALLTYPE CreateTransformGroup(IDCompositionTransform **, UINT,
                                                   IDCompositionTransform **out) override
    {
        return unsupported_out(out, "CreateTransformGroup");
    }
    HRESULT STDMETHODCALLTYPE CreateTransform3DGroup(IDCompositionTransform3D **, UINT,
                                                     IDCompositionTransform3D **out) override
    {
        return unsupported_out(out, "CreateTransform3DGroup");
    }
    HRESULT STDMETHODCALLTYPE CreateTargetForHwnd(HWND hwnd, BOOL topmost, IDCompositionTarget **out) override;
    HRESULT STDMETHODCALLTYPE CreateSurfaceFromHandle(HANDLE, IUnknown **out) override
    {
        return unsupported_out(out, "CreateSurfaceFromHandle");
    }
    HRESULT STDMETHODCALLTYPE CreateSurfaceFromHwnd(HWND, IUnknown **out) override
    {
        return unsupported_out(out, "CreateSurfaceFromHwnd");
    }
    HRESULT set_event(HANDLE event);
    HRESULT open_shared(HANDLE handle, REFIID iid, void **out);
};

std::vector<Device *> Device::registry;
CRITICAL_SECTION Device::registry_mutex;
INIT_ONCE Device::registry_once = INIT_ONCE_STATIC_INIT;
INIT_ONCE Device::refresh_class_once = INIT_ONCE_STATIC_INIT;

BOOL CALLBACK Device::initialize_registry(PINIT_ONCE, PVOID, PVOID *)
{
    InitializeCriticalSection(&registry_mutex);
    return TRUE;
}

void Device::register_device(Device *device)
{
    InitOnceExecuteOnce(&registry_once, initialize_registry, nullptr, nullptr);
    Lock lock(registry_mutex);
    registry.push_back(device);
}

BOOL CALLBACK Device::initialize_refresh_class(PINIT_ONCE, PVOID, PVOID *)
{
    WNDCLASSW wc = {};
    wc.lpfnWndProc = refresh_window_proc;
    wc.hInstance = GetModuleHandleW(L"dcomp.dll");
    wc.lpszClassName = shared_refresh_class;
    if (RegisterClassW(&wc))
        return TRUE;
    return GetLastError() == ERROR_CLASS_ALREADY_EXISTS;
}

HRESULT Device::create_refresh_window()
{
    if (!InitOnceExecuteOnce(&refresh_class_once, initialize_refresh_class, nullptr, nullptr))
        return HRESULT_FROM_WIN32(GetLastError());
    HWND value = CreateWindowExW(0, shared_refresh_class, L"", 0, 0, 0, 0, 0,
                                 HWND_MESSAGE, nullptr, GetModuleHandleW(L"dcomp.dll"), this);
    if (!value)
        return HRESULT_FROM_WIN32(GetLastError());
    { Lock lock(registry_mutex); refresh_window = value; }
    return S_OK;
}

class Surface final : public IDCompositionSurface
{
    LONG refs = 1;
    Ref<ID3D11Texture2D> update;
    Ref<ID2D1DeviceContext> draw;
    RECT region = {};
    SurfaceInterface partner = {surface_partner_table, this};

  public:
    Device *device;
    Renderer renderer;
    UINT width = 0, height = 0;
    DXGI_FORMAT format = DXGI_FORMAT_B8G8R8A8_UNORM;
    DXGI_ALPHA_MODE alpha = DXGI_ALPHA_MODE_IGNORE;
    std::vector<BYTE> pixels;
    bool initialized = false;
    explicit Surface(Device *owner) : device(owner)
    {
        if (device) device->AddRef();
        InterlockedIncrement(&objects);
    }
    ~Surface()
    {
        if (device)
        {
            Lock lock(device->mutex);
            cleanup_draw();
            erase(device->surfaces, this);
        }
        if (device) device->Release();
        InterlockedDecrement(&objects);
    }
    void cleanup_draw()
    {
        if (draw.p)
        {
            draw->SetTarget(nullptr);
            draw.reset();
        }
        update.reset();
        if (device->drawing == this)
            device->drawing = nullptr;
    }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void **out) override
    {
        if (!out)
            return E_POINTER;
        *out = nullptr;
        if (iid == __uuidof(IUnknown) || iid == surface_iid)
            *out = static_cast<IDCompositionSurface *>(this);
        else if (iid == surface_partner_iid)
            *out = &partner;
        else
            return E_NOINTERFACE;
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override
    {
        return InterlockedIncrement(&refs);
    }
    ULONG STDMETHODCALLTYPE Release() override
    {
        ULONG value = InterlockedDecrement(&refs);
        if (!value)
            delete this;
        return value;
    }
    HRESULT STDMETHODCALLTYPE BeginDraw(const RECT *rect, REFIID iid, void **out, POINT *offset) override;
    HRESULT STDMETHODCALLTYPE EndDraw() override;
    HRESULT resize(UINT w, UINT h);
    HRESULT STDMETHODCALLTYPE SuspendDraw() override
    {
        return unsupported("SuspendDraw");
    }
    HRESULT STDMETHODCALLTYPE ResumeDraw() override
    {
        return unsupported("ResumeDraw");
    }
    HRESULT STDMETHODCALLTYPE Scroll(const RECT *, const RECT *, int, int) override
    {
        return unsupported("Scroll");
    }
};

HRESULT Surface::resize(UINT w, UINT h)
{
    Lock lock(device->mutex);
    // Native logical surface: active draw is rejected before argument checks.
    // Zero is allowed only as a pair; it leaves a nondrawable empty surface.
    HRESULT hr = S_OK;
    if (device->drawing == this)
        hr = static_cast<HRESULT>(0x88980801); // DCOMPOSITION_ERROR_SURFACE_BEING_RENDERED
    else if ((!w != !h) || w > 16384 || h > 16384)
        hr = E_INVALIDARG;
    else
    {
        try
        {
            // Allocate before mutating so allocation failure preserves content
            // and dimensions. Native 060ab4 clears validity even at equal size.
            std::vector<BYTE> replacement(static_cast<size_t>(w) * h * 4);
            pixels.swap(replacement);
            width = w;
            height = h;
            initialized = false;
        }
        catch (const std::bad_alloc &)
        {
            hr = E_OUTOFMEMORY;
        }
    }
    if (GetEnvironmentVariableA("GHUB_DCOMP_TRACE", nullptr, 0))
        std::fprintf(stderr, "dcomp ResizeSurface: surface=%p requested=%ux%u actual=%ux%u hr=%08lx\n",
                     this, w, h, width, height, static_cast<unsigned long>(hr));
    return hr;
}

static HRESULT make_surface(Device *device, Renderer &renderer, UINT w, UINT h, DXGI_FORMAT format,
                            DXGI_ALPHA_MODE alpha, IDCompositionSurface **out)
{
    if (GetEnvironmentVariableA("GHUB_DCOMP_TRACE", nullptr, 0))
        std::fprintf(stderr, "dcomp CreateSurface: device=%p size=%ux%u format=%u alpha=%u\n",
                     device, w, h, static_cast<UINT>(format), static_cast<UINT>(alpha));
    if (!out)
        return E_POINTER;
    *out = nullptr;
    if (!w || !h || w > 16384 || h > 16384 || !renderer.d3d.p)
        return E_INVALIDARG;
    if (format != DXGI_FORMAT_B8G8R8A8_UNORM && format != DXGI_FORMAT_R8G8B8A8_UNORM)
        return unsupported("surface format");
    if (alpha != DXGI_ALPHA_MODE_PREMULTIPLIED && alpha != DXGI_ALPHA_MODE_IGNORE &&
        alpha != DXGI_ALPHA_MODE_UNSPECIFIED)
        return E_INVALIDARG;
    try
    {
        Ref<Surface> surface;
        surface.p = new Surface(device);
        surface->width = w;
        surface->height = h;
        surface->format = format;
        surface->alpha = alpha;
        surface->pixels.resize(static_cast<size_t>(w) * h * 4);
        surface->renderer.d3d.retain(renderer.d3d.p);
        surface->renderer.context.retain(renderer.context.p);
        surface->renderer.d2d.retain(renderer.d2d.p);
        device->surfaces.push_back(surface.p);
        *out = surface.detach();
        return S_OK;
    }
    catch (const std::bad_alloc &)
    {
        return E_OUTOFMEMORY;
    }
}

HRESULT Surface::BeginDraw(const RECT *rect, REFIID iid, void **out, POINT *offset)
{
    if (!out)
        return E_POINTER;
    *out = nullptr;
    if (!offset)
        return E_POINTER;
    *offset = {};
    Lock lock(device->mutex);
    if (device->drawing)
        return HRESULT_FROM_WIN32(ERROR_BUSY);
    if (!width || !height)
        return static_cast<HRESULT>(0x8000000e); // E_ILLEGAL_METHOD_CALL on an empty logical surface
    RECT requested = rect ? *rect : RECT{0, 0, static_cast<LONG>(width), static_cast<LONG>(height)};
    if (requested.left < 0 || requested.top < 0 || requested.right > static_cast<LONG>(width) ||
        requested.bottom > static_cast<LONG>(height) || requested.left >= requested.right ||
        requested.top >= requested.bottom)
        return E_INVALIDARG;
    if (!initialized && (requested.left || requested.top || requested.right != static_cast<LONG>(width) ||
                         requested.bottom != static_cast<LONG>(height)))
        return E_INVALIDARG;
    bool use_d2d = iid == __uuidof(ID2D1DeviceContext);
    // The bundled dwmcorei drawing path requests the texture itself (IID at
    // RVA 1da6f0), then reads ID3D11Texture2D::GetDesc before wrapping it in D2D.
    bool use_texture = iid == __uuidof(ID3D11Texture2D);
    if (use_d2d && !renderer.d2d.p)
        return E_INVALIDARG;
    if (!use_d2d && !use_texture && iid != __uuidof(IDXGISurface))
        return E_NOINTERFACE;
    D3D11_TEXTURE2D_DESC desc = {};
    desc.Width = requested.right - requested.left;
    desc.Height = requested.bottom - requested.top;
    desc.MipLevels = desc.ArraySize = desc.SampleDesc.Count = 1;
    desc.Format = format;
    desc.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    HRESULT hr = renderer.d3d->CreateTexture2D(&desc, nullptr, &update.p);
    Ref<IDXGISurface> dxgi;
    if (SUCCEEDED(hr))
        hr = update->QueryInterface(__uuidof(IDXGISurface), reinterpret_cast<void **>(&dxgi.p));
    if (SUCCEEDED(hr) && use_d2d)
    {
        hr = renderer.d2d->CreateDeviceContext(D2D1_DEVICE_CONTEXT_OPTIONS_NONE, &draw.p);
        if (SUCCEEDED(hr))
        {
            D2D1_BITMAP_PROPERTIES1 properties = {};
            properties.pixelFormat = {desc.Format, alpha == DXGI_ALPHA_MODE_PREMULTIPLIED
                                                       ? D2D1_ALPHA_MODE_PREMULTIPLIED
                                                       : D2D1_ALPHA_MODE_IGNORE};
            properties.dpiX = properties.dpiY = 96;
            properties.bitmapOptions = D2D1_BITMAP_OPTIONS_TARGET | D2D1_BITMAP_OPTIONS_CANNOT_DRAW;
            Ref<ID2D1Bitmap1> bitmap;
            hr = draw->CreateBitmapFromDxgiSurface(dxgi.p, &properties, &bitmap.p);
            if (SUCCEEDED(hr))
            {
                draw->SetTarget(bitmap.p);
                draw->BeginDraw();
                draw->AddRef();
                *out = draw.p;
            }
        }
    }
    else if (SUCCEEDED(hr))
    {
        if (use_texture)
            hr = update->QueryInterface(iid, out);
        else
            *out = dxgi.detach();
    }
    if (FAILED(hr))
    {
        cleanup_draw();
        return hr;
    }
    region = requested;
    device->drawing = this;
    return S_OK;
}

HRESULT Surface::EndDraw()
{
    Lock lock(device->mutex);
    if (device->drawing != this || !update.p)
        return E_UNEXPECTED;
    HRESULT hr = draw.p ? draw->EndDraw() : S_OK;
    Ref<ID3D11Texture2D> staging;
    D3D11_TEXTURE2D_DESC desc = {};
    update->GetDesc(&desc);
    desc.BindFlags = 0;
    desc.Usage = D3D11_USAGE_STAGING;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    if (SUCCEEDED(hr))
        hr = renderer.d3d->CreateTexture2D(&desc, nullptr, &staging.p);
    if (SUCCEEDED(hr))
    {
        renderer.context->CopyResource(staging.p, update.p);
        D3D11_MAPPED_SUBRESOURCE map = {};
        hr = renderer.context->Map(staging.p, 0, D3D11_MAP_READ, 0, &map);
        if (SUCCEEDED(hr))
        {
            for (UINT y = 0; y < desc.Height; ++y)
            {
                BYTE *dest = pixels.data() + (static_cast<size_t>(region.top + y) * width + region.left) * 4;
                memcpy(dest, static_cast<const BYTE *>(map.pData) + y * map.RowPitch, desc.Width * 4);
                // The update texture exposes the caller's exact DXGI format.
                // Retained frames and the window publisher use canonical BGRA8.
                if (format == DXGI_FORMAT_R8G8B8A8_UNORM)
                    for (UINT x = 0; x < desc.Width; ++x)
                        std::swap(dest[x * 4], dest[x * 4 + 2]);
                if (alpha != DXGI_ALPHA_MODE_PREMULTIPLIED)
                    for (UINT x = 0; x < desc.Width; ++x)
                        dest[x * 4 + 3] = 255;
            }
            renderer.context->Unmap(staging.p, 0);
            initialized = true;
        }
    }
    cleanup_draw();
    return hr;
}

class Visual final : public IDCompositionVisual2, public VisualOwner
{
    LONG refs = 1;

  public:
    Device *device;
    Visual *parent = nullptr;
    Ref<Surface> content;
    Ref<SharedResource> imported;
    SharedFrame snapshot;
    std::vector<Visual *> children;
    double x = 0, y = 0;
    RasterModes modes{DCOMPOSITION_BITMAP_INTERPOLATION_MODE_INHERIT,
                      DCOMPOSITION_BORDER_MODE_INHERIT, DCOMPOSITION_BACKFACE_VISIBILITY_INHERIT};
    LayoutSize size, relative_size, relative_offset;
    VisualLayoutInterface layout_interface;
    bool clipped = false;
    RasterRect clip = {};
    Ref<InputBinding> pending_input, committed_input;
    explicit Visual(Device *owner) : device(owner)
    {
        layout_interface = {visual_layout_table, this};
        if (device) device->AddRef();
        InterlockedIncrement(&objects);
    }
    ~Visual()
    {
        if (device)
        {
            Lock lock(device->mutex);
            RemoveAllVisuals();
            content.reset();
            pending_input.reset();
            committed_input.reset();
            erase(device->visuals, this);
        }
        else
            for (auto *child : children) child->Release();
        if (device) device->Release();
        InterlockedDecrement(&objects);
    }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void **out) override
    {
        if (!out)
            return E_POINTER;
        *out = nullptr;
        if (iid == visual_owner_iid)
            *out = static_cast<VisualOwner *>(this);
        else if (iid == visual_restricted)
            *out = &layout_interface;
        else if (iid == __uuidof(IUnknown) || iid == visual_iid || iid == __uuidof(IDCompositionVisual2))
            *out = static_cast<IDCompositionVisual2 *>(this);
        else
        {
            std::fprintf(stderr, "dcomp visual unsupported IID=%08lx-%04x-%04x\n", iid.Data1, iid.Data2, iid.Data3);
            return E_NOINTERFACE;
        }
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override
    {
        return InterlockedIncrement(&refs);
    }
    HRESULT STDMETHODCALLTYPE GetOwner(Device **out) override
    {
        if (!out) return E_POINTER;
        *out = device;
        device->AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE Release() override
    {
        ULONG value = InterlockedDecrement(&refs);
        if (!value)
            delete this;
        return value;
    }
    static bool coordinate(double value)
    {
        return std::isfinite(value) && value >= -1000000 && value <= 1000000;
    }
    HRESULT STDMETHODCALLTYPE SetOffsetX(float value) override
    {
        if (!std::isfinite(value)) return E_INVALIDARG;
        if (!coordinate(value)) return unsupported("large offset");
        Lock lock(device->mutex);
        x = value;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE SetOffsetY(float value) override
    {
        if (!std::isfinite(value)) return E_INVALIDARG;
        if (!coordinate(value)) return unsupported("large offset");
        Lock lock(device->mutex);
        y = value;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE SetOffsetX(IDCompositionAnimation *) override
    {
        return unsupported("animated offset X");
    }
    HRESULT STDMETHODCALLTYPE SetOffsetY(IDCompositionAnimation *) override
    {
        return unsupported("animated offset Y");
    }
    HRESULT STDMETHODCALLTYPE SetTransform(IDCompositionTransform *) override
    {
        return unsupported("SetTransform object");
    }
    HRESULT STDMETHODCALLTYPE SetTransform(const D2D_MATRIX_3X2_F &) override
    {
        return unsupported("SetTransform matrix");
    }
    HRESULT STDMETHODCALLTYPE SetTransformParent(IDCompositionVisual *) override
    {
        return unsupported("SetTransformParent");
    }
    HRESULT STDMETHODCALLTYPE SetEffect(IDCompositionEffect *) override
    {
        return unsupported("SetEffect");
    }
    HRESULT STDMETHODCALLTYPE SetBitmapInterpolationMode(DCOMPOSITION_BITMAP_INTERPOLATION_MODE value) override
    {
        if (value != DCOMPOSITION_BITMAP_INTERPOLATION_MODE_NEAREST_NEIGHBOR &&
            value != DCOMPOSITION_BITMAP_INTERPOLATION_MODE_LINEAR &&
            value != DCOMPOSITION_BITMAP_INTERPOLATION_MODE_INHERIT) return E_INVALIDARG;
        Lock lock(device->mutex);
        modes.interpolation = value;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE SetBorderMode(DCOMPOSITION_BORDER_MODE value) override
    {
        if (value != DCOMPOSITION_BORDER_MODE_SOFT && value != DCOMPOSITION_BORDER_MODE_HARD &&
            value != DCOMPOSITION_BORDER_MODE_INHERIT) return E_INVALIDARG;
        Lock lock(device->mutex);
        modes.border = value;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE SetClip(IDCompositionClip *value) override
    {
        if (value)
            return unsupported("clip object");
        Lock lock(device->mutex);
        clipped = false;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE SetClip(const D2D_RECT_F &value) override
    {
        if (!coordinate(value.left) || !coordinate(value.top) || !coordinate(value.right) || !coordinate(value.bottom))
            return E_INVALIDARG;
        if (value.left > value.right || value.top > value.bottom)
            return E_INVALIDARG;
        Lock lock(device->mutex);
        clip = {value.left, value.top, value.right, value.bottom};
        clipped = true;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE SetContent(IUnknown *value) override;
    HRESULT STDMETHODCALLTYPE AddVisual(IDCompositionVisual *value, BOOL above,
                                        IDCompositionVisual *reference) override;
    HRESULT STDMETHODCALLTYPE RemoveVisual(IDCompositionVisual *value) override;
    HRESULT STDMETHODCALLTYPE RemoveAllVisuals() override
    {
        Lock lock(device->mutex);
        for (auto *child : children)
        {
            child->parent = nullptr;
            child->Release();
        }
        children.clear();
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE SetCompositeMode(DCOMPOSITION_COMPOSITE_MODE value) override
    {
        return value == DCOMPOSITION_COMPOSITE_MODE_SOURCE_OVER || value == DCOMPOSITION_COMPOSITE_MODE_INHERIT
                   ? S_OK
                   : unsupported("composite mode");
    }
    HRESULT STDMETHODCALLTYPE SetOpacityMode(DCOMPOSITION_OPACITY_MODE) override
    {
        return unsupported("SetOpacityMode");
    }
    HRESULT STDMETHODCALLTYPE SetBackFaceVisibility(DCOMPOSITION_BACKFACE_VISIBILITY value) override
    {
        if (value != DCOMPOSITION_BACKFACE_VISIBILITY_VISIBLE && value != DCOMPOSITION_BACKFACE_VISIBILITY_HIDDEN &&
            value != DCOMPOSITION_BACKFACE_VISIBILITY_INHERIT) return E_INVALIDARG;
        Lock lock(device->mutex);
        modes.backface = value;
        return S_OK;
    }
    HRESULT set_layout(unsigned slot, float value);
    HRESULT set_input(HANDLE value);
    HRESULT validate_input(unsigned = 0) const;
    HRESULT input_regions(std::vector<InputRegion> &, double, double, RasterRect,
                          LayoutSize = {}, InputBinding * = nullptr, unsigned = 0) const;
    HRESULT geometry(LayoutSize parent_size, LayoutSize &effective, double &dx, double &dy) const;
    HRESULT render(std::vector<BYTE> &dest, UINT width, UINT height, double ox, double oy, RECT bounds, unsigned depth,
                   LayoutSize parent_size = {}, RasterModes inherited = {}, size_t scratch_budget = 256u * 1024u * 1024u);
    HRESULT extent(RECT &area, double ox, double oy, unsigned depth, LayoutSize parent_size = {});
    bool contains_resource(const SharedResource *resource, unsigned depth) const;
    // Detached, private render copies have no device references or GPU state;
    // no COM interface to these copies is handed to application code.
    HRESULT freeze(Visual **out, size_t &budget, unsigned depth = 0) const;
    HRESULT sample_imports(unsigned depth = 0);
};

HRESULT Visual::freeze(Visual **out, size_t &budget, unsigned depth) const
{
    *out = nullptr;
    if (depth > 128) return E_INVALIDARG;
    if (budget < sizeof(Visual)) return E_OUTOFMEMORY;
    budget -= sizeof(Visual);
    Ref<Visual> copy;
    copy.p = new Visual(nullptr);
    copy->x = x; copy->y = y; copy->modes = modes;
    copy->size = size; copy->relative_size = relative_size; copy->relative_offset = relative_offset;
    copy->clipped = clipped; copy->clip = clip;
    copy->pending_input.retain(pending_input.p);
    copy->imported.retain(imported.p);
    if (content.p)
    {
        if (!device) copy->content.retain(content.p); // already immutable
        else
        {
            if (budget < content->pixels.size()) return E_OUTOFMEMORY;
            budget -= content->pixels.size();
            copy->content.p = new Surface(nullptr);
            copy->content->width = content->width; copy->content->height = content->height;
            copy->content->initialized = content->initialized;
            copy->content->pixels = content->pixels;
        }
    }
    copy->children.reserve(children.size());
    for (auto *child : children)
    {
        Ref<Visual> next;
        HRESULT hr = child->freeze(&next.p, budget, depth + 1);
        if (FAILED(hr)) return hr;
        copy->children.push_back(next.detach());
    }
    *out = copy.detach();
    return S_OK;
}

HRESULT Visual::sample_imports(unsigned depth)
{
    if (depth > 128) return E_INVALIDARG;
    if (imported.p)
    {
        HRESULT hr = imported->read(snapshot);
        if (FAILED(hr)) return hr;
    }
    for (auto *child : children)
    {
        HRESULT hr = child->sample_imports(depth + 1);
        if (FAILED(hr)) return hr;
    }
    return S_OK;
}

HRESULT Visual::set_input(HANDLE value)
{
    Lock lock(device->mutex);
    if (!value)
    {
        pending_input.reset();
        return S_OK;
    }
    Ref<InputBinding> input;
    HRESULT hr = InputBinding::open(value, &input.p);
    if (FAILED(hr)) return hr;
    // Handles can be duplicated or reused; only the kernel LUID identifies
    // the sink. A repeated assignment must not accumulate retained handles.
    if (!pending_input.p || !pending_input->same(*input.p))
        pending_input.retain(input.p);
    if (GetEnvironmentVariableA("GHUB_DCOMP_TRACE", nullptr, 0))
        std::fprintf(stderr, "dcomp SetInput: visual=%p caller_handle=%p luid=%08lx:%08lx retained=%p\n",
                     this, value, static_cast<ULONG>(input->id.HighPart), input->id.LowPart,
                     pending_input->handle);
    return S_OK;
}

HRESULT Visual::validate_input(unsigned depth) const
{
    if (depth > 128) return E_INVALIDARG;
    if (pending_input.p)
    {
        // Validate even fully clipped / empty visuals before publishing any
        // destination. Unsupported routing categories must not disappear.
        std::vector<InputRegion> one;
        one.emplace_back(RasterRect{0, 0, 1, 1}, pending_input.p);
        Ref<InputScene> scene;
        HRESULT hr = InputScene::create(one, &scene.p);
        if (FAILED(hr)) return hr;
    }
    for (auto *child : children)
    {
        HRESULT hr = child->validate_input(depth + 1);
        if (FAILED(hr)) return hr;
    }
    return S_OK;
}
HRESULT Visual::input_regions(std::vector<InputRegion> &out, double ox, double oy, RasterRect bounds,
                              LayoutSize parent_size, InputBinding *inherited, unsigned depth) const
{
    if (depth > 128) return E_INVALIDARG;
    LayoutSize effective;
    double dx, dy;
    HRESULT hr = geometry(parent_size, effective, dx, dy);
    if (FAILED(hr)) return hr;
    ox += dx; oy += dy;
    if (clipped)
    {
        bounds.left = std::max(bounds.left, clip.left + ox);
        bounds.top = std::max(bounds.top, clip.top + oy);
        bounds.right = std::min(bounds.right, clip.right + ox);
        bounds.bottom = std::min(bounds.bottom, clip.bottom + oy);
    }
    if (bounds.left >= bounds.right || bounds.top >= bounds.bottom) return S_OK;
    InputBinding *sink = pending_input.p ? pending_input.p : inherited;
    auto add = [&](RasterRect rect, InputBinding *binding) -> HRESULT
    {
        if (!binding) return S_OK;
        rect.left = std::max(bounds.left, rect.left + ox);
        rect.top = std::max(bounds.top, rect.top + oy);
        rect.right = std::min(bounds.right, rect.right + ox);
        rect.bottom = std::min(bounds.bottom, rect.bottom + oy);
        if (rect.left >= rect.right || rect.top >= rect.bottom) return S_OK;
        if (out.size() >= 4096) return E_INVALIDARG;
        out.emplace_back(rect, binding);
        return S_OK;
    };
    hr = add({0, 0, effective.width, effective.height}, sink);
    if (FAILED(hr)) return hr;
    if (content.p && content->initialized)
    {
        hr = add({0, 0, double(content->width), double(content->height)}, sink);
        if (FAILED(hr)) return hr;
    }
    if (imported.p)
    {
        hr = add({double(snapshot.area.left), double(snapshot.area.top),
                  double(snapshot.area.right), double(snapshot.area.bottom)}, sink);
        if (FAILED(hr)) return hr;
        for (const auto &region : snapshot.inputs)
        {
            hr = add(region.area, region.sink.p);
            if (FAILED(hr)) return hr;
        }
    }
    // Rendering and hit testing share bottom-to-top child order. The server
    // scans this immutable list backwards to pick the foremost input region.
    for (auto *child : children)
    {
        hr = child->input_regions(out, ox, oy, bounds, effective, sink, depth + 1);
        if (FAILED(hr)) return hr;
    }
    return S_OK;
}

template <class T> static IUnknown *identity_of(T *value) { return static_cast<IUnknown *>(value); }
static IUnknown *identity_of(Visual *value) { return static_cast<IDCompositionVisual2 *>(value); }
template <class T> static T *lookup(const std::vector<T *> &list, IUnknown *input)
{
    if (!input)
        return nullptr;
    Ref<IUnknown> identity;
    if (FAILED(input->QueryInterface(__uuidof(IUnknown), reinterpret_cast<void **>(&identity.p))))
        return nullptr;
    for (auto *value : list)
        if (identity_of(value) == identity.p)
            return value;
    return nullptr;
}
HRESULT Visual::SetContent(IUnknown *value)
{
    Lock lock(device->mutex);
    Surface *surface = lookup(device->surfaces, value);
    if (value && !surface)
        return unsupported("external visual content (swapchain/shared surface)");
    content.retain(surface);
    return S_OK;
}
HRESULT Visual::AddVisual(IDCompositionVisual *value, BOOL above, IDCompositionVisual *reference)
{
    Lock lock(device->mutex);
    Visual *child = lookup(device->visuals, value), *ref = lookup(device->visuals, reference);
    if (!child || child->parent || (reference && (!ref || ref->parent != this)))
        return E_INVALIDARG;
    for (Visual *p = this; p; p = p->parent)
        if (p == child)
            return E_INVALIDARG;
    try
    {
        auto where =
            reference ? std::find(children.begin(), children.end(), ref) : (above ? children.end() : children.begin());
        if (reference && above)
            ++where;
        children.insert(where, child);
        child->AddRef();
        child->parent = this;
        return S_OK;
    }
    catch (const std::bad_alloc &)
    {
        return E_OUTOFMEMORY;
    }
}
HRESULT Visual::RemoveVisual(IDCompositionVisual *value)
{
    Lock lock(device->mutex);
    Visual *child = lookup(device->visuals, value);
    if (!child || child->parent != this)
        return E_INVALIDARG;
    erase(children, child);
    child->parent = nullptr;
    child->Release();
    return S_OK;
}
bool Visual::contains_resource(const SharedResource *resource, unsigned depth) const
{
    if (depth > 128 || (imported.p && (!resource || imported->same(resource)))) return true;
    for (auto *child : children)
        if (child->contains_resource(resource, depth + 1)) return true;
    return false;
}
HRESULT Visual::set_layout(unsigned slot, float value)
{
    // Native CVisualProxy setters validate finiteness, including fixed sizes;
    // negative effective dimensions are clamped during layout, not at Set.
    if (!std::isfinite(value)) return E_INVALIDARG;
    Lock lock(device->mutex);
    switch (slot)
    {
    case 36: size.width = value; break;
    case 37: size.height = value; break;
    case 38: relative_offset.width = value; break;
    case 39: relative_offset.height = value; break;
    case 40: relative_size.width = value; break;
    case 41: relative_size.height = value; break;
    default: return E_INVALIDARG;
    }
    if (GetEnvironmentVariableA("GHUB_DCOMP_TRACE", nullptr, 0))
        std::fprintf(stderr, "dcomp visual layout: slot=%u value=%g\n", slot, static_cast<double>(value));
    return S_OK;
}
HRESULT Visual::geometry(LayoutSize parent_size, LayoutSize &effective, double &dx, double &dy) const
{
    // Matched dwmcorei CVisual::UpdateLayoutSize RVA1454a0: fixed size plus
    // relative size times parent's effective size; an unattached root uses 0.
    // This does NOT implicitly stretch/clip ordinary surface content.
    float width = size.width + relative_size.width * parent_size.width;
    float height = size.height + relative_size.height * parent_size.height;
    double offset_x = x + static_cast<double>(relative_offset.width) * parent_size.width;
    double offset_y = y + static_cast<double>(relative_offset.height) * parent_size.height;
    if (!std::isfinite(width) || !std::isfinite(height)) return E_INVALIDARG;
    if (!coordinate(offset_x) || !coordinate(offset_y))
        return unsupported("large/nonfinite effective visual offset");
    effective = {std::max(0.0f, width), std::max(0.0f, height)};
    dx = offset_x;
    dy = offset_y;
    return S_OK;
}
HRESULT Visual::extent(RECT &area, double ox, double oy, unsigned depth, LayoutSize parent_size)
{
    if (depth > 128) return E_INVALIDARG;
    area = {};
    LayoutSize effective;
    double dx, dy;
    HRESULT hr = geometry(parent_size, effective, dx, dy);
    if (FAILED(hr)) return hr;
    ox += dx;
    oy += dy;
    if (content.p && content->initialized)
    {
        hr = RasterRect{ox, oy, ox + content->width, oy + content->height}.bounds(area);
        if (FAILED(hr)) return hr;
    }
    if (imported.p && !IsRectEmpty(&snapshot.area))
    {
        RECT imported_area;
        hr = RasterRect{snapshot.area.left + ox, snapshot.area.top + oy,
                        snapshot.area.right + ox, snapshot.area.bottom + oy}.bounds(imported_area);
        if (FAILED(hr)) return hr;
        UnionRect(&area, &area, &imported_area);
    }
    for (auto *child : children)
    {
        RECT child_area;
        hr = child->extent(child_area, ox, oy, depth + 1, effective);
        if (FAILED(hr)) return hr;
        UnionRect(&area, &area, &child_area);
    }
    if (clipped)
    {
        RECT translated;
        hr = RasterRect{clip.left + ox, clip.top + oy, clip.right + ox, clip.bottom + oy}.bounds(translated);
        if (FAILED(hr)) return hr;
        IntersectRect(&area, &area, &translated);
    }
    return S_OK;
}
HRESULT Visual::render(std::vector<BYTE> &dest, UINT width, UINT height, double ox, double oy, RECT bounds, unsigned depth,
                       LayoutSize parent_size, RasterModes inherited, size_t scratch_budget)
{
    if (depth > 128) return unsupported("visual tree depth > 128");
    LayoutSize effective;
    double dx, dy;
    HRESULT hr = geometry(parent_size, effective, dx, dy);
    if (FAILED(hr)) return hr;
    ox += dx;
    oy += dy;
    RasterModes resolved = modes.inherit(inherited);
    // Translation cannot turn a surface away from the observer. Both HIDDEN
    // and VISIBLE therefore draw these front faces. 3D transforms still fail
    // explicitly; when implemented they must supply their actual orientation.
    if (!resolved.visible(true)) return S_OK;
    RasterRect translated{clip.left + ox, clip.top + oy, clip.right + ox, clip.bottom + oy};
    std::vector<BYTE> layer;
    std::vector<BYTE> *output = &dest;
    RECT destination_bounds = bounds;
    UINT output_width = width, output_height = height;
    if (clipped)
    {
        RECT local;
        hr = translated.bounds(local, resolved.border == DCOMPOSITION_BORDER_MODE_SOFT);
        if (FAILED(hr)) return hr;
        if (!IntersectRect(&bounds, &bounds, &local)) return S_OK;
        destination_bounds = bounds;
        if (resolved.border == DCOMPOSITION_BORDER_MODE_SOFT && !translated.integral())
        {
            // A visual's clip applies to its composed subtree, not independently
            // to every overlapping child (which would over-count edge alpha).
            // Only allocate the clipped region, with a cumulative per-tree
            // budget so deeply nested clips cannot exhaust the workstation.
            output_width = bounds.right - bounds.left;
            output_height = bounds.bottom - bounds.top;
            size_t bytes = static_cast<size_t>(output_width) * output_height * 4;
            if (bytes > scratch_budget) return E_OUTOFMEMORY;
            scratch_budget -= bytes;
            layer.assign(bytes, 0);
            output = &layer;
            ox -= bounds.left;
            oy -= bounds.top;
            bounds = {0, 0, static_cast<LONG>(output_width), static_cast<LONG>(output_height)};
        }
    }
    if (content.p && content->initialized)
    {
        hr = raster_bitmap(*output, output_width, content->pixels.data(), content->width, content->height,
                           ox, oy, bounds, resolved);
        if (FAILED(hr)) return hr;
    }
    if (imported.p && !snapshot.pixels.empty())
    {
        hr = raster_bitmap(*output, output_width, snapshot.pixels.data(), snapshot.area.right - snapshot.area.left,
                           snapshot.area.bottom - snapshot.area.top,
                           snapshot.area.left + ox, snapshot.area.top + oy, bounds, resolved);
        if (FAILED(hr)) return hr;
    }
    for (auto *child : children)
    {
        hr = child->render(*output, output_width, output_height, ox, oy, bounds, depth + 1, effective, resolved, scratch_budget);
        if (FAILED(hr)) return hr;
    }
    if (output == &layer)
        for (LONG py = destination_bounds.top; py < destination_bounds.bottom; ++py)
            for (LONG px = destination_bounds.left; px < destination_bounds.right; ++px)
            {
                size_t index = (static_cast<size_t>(py) * width + px) * 4;
                size_t source = (static_cast<size_t>(py - destination_bounds.top) * output_width
                                 + px - destination_bounds.left) * 4;
                double coverage = translated.coverage(px, py);
                BYTE sample[4];
                for (unsigned c = 0; c < 4; ++c)
                    sample[c] = static_cast<BYTE>(std::lround(layer[source + c] * coverage));
                source_over(dest.data() + index, sample);
            }
    return S_OK;
}

// A shared target publishes its owner's committed visual tree into the relay.
// Same-process readers are scheduled onto their owning apartment after a
// successful publication; cross-process readers sample on their next Commit.
// The first implementation intentionally has one live publisher and rejects
// direct feedback and publisher changes across devices; no native DWM IPC ABI.
class SharedTarget final : public IDCompositionTarget
{
    LONG refs = 1;
    Device *device = nullptr;
    Ref<Visual> root;
    Ref<SharedResource> resource;
    GUID publisher = {};
    CRITICAL_SECTION changes;
    SharedFrame published_frame;
    bool published = false;

  public:
    Ref<Visual> committed_root;
    bool committed = false;
    Visual *pending_root() const { return root.p; }
    explicit SharedTarget(SharedResource *value)
    {
        InitializeCriticalSection(&changes);
        resource.retain(value);
        InterlockedIncrement(&objects);
    }
    ~SharedTarget()
    {
        if (device)
        {
            {
                Lock lock(device->mutex);
                erase(device->shared_targets, this);
                root.reset();
            }
            device->Release();
        }
        resource->unclaim(publisher);
        DeleteCriticalSection(&changes);
        InterlockedDecrement(&objects);
    }
    HRESULT initialize(Device *owner)
    {
        HRESULT hr = CoCreateGuid(&publisher);
        if (SUCCEEDED(hr)) hr = resource->claim(publisher);
        if (SUCCEEDED(hr) && owner) hr = bind(owner);
        return hr;
    }
    HRESULT bind(Device *owner)
    {
        Lock lock(owner->mutex);
        owner->shared_targets.push_back(this);
        owner->AddRef();
        device = owner;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void **out) override
    {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid != __uuidof(IUnknown) && iid != target_iid) return E_NOINTERFACE;
        *out = static_cast<IDCompositionTarget *>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&refs); }
    ULONG STDMETHODCALLTYPE Release() override
    {
        ULONG value = InterlockedDecrement(&refs);
        if (!value) delete this;
        return value;
    }
    HRESULT STDMETHODCALLTYPE SetRoot(IDCompositionVisual *value) override
    {
        Lock changing(changes);
        try
        {
            if (value && !device)
            {
                Ref<VisualOwner> ownership;
                Ref<Device> owner;
                HRESULT hr = value->QueryInterface(visual_owner_iid, reinterpret_cast<void **>(&ownership.p));
                if (FAILED(hr)) return E_INVALIDARG;
                hr = ownership->GetOwner(&owner.p);
                if (FAILED(hr)) return hr;
                bind(owner.p);
            }
            if (!device) return S_OK; // detached target with no root yet
            Lock lock(device->mutex);
            Visual *visual = lookup(device->visuals, value);
            if (value && (!visual || visual->contains_resource(resource.p, 0))) return E_INVALIDARG;
            root.retain(visual);
            return S_OK;
        }
        catch (const std::bad_alloc &) { return E_OUTOFMEMORY; }
    }
    HRESULT prepare(SharedFrame &frame, Visual *tree)
    {
        frame = {};
        if (!tree) return S_OK;
        if (tree->contains_resource(resource.p, 0)) return E_INVALIDARG;
        HRESULT hr = tree->input_regions(frame.inputs, 0, 0, {-128000000, -128000000, 128000000, 128000000});
        if (SUCCEEDED(hr)) hr = InputScene::create(frame.inputs, &frame.input_scene.p);
        if (SUCCEEDED(hr)) hr = tree->extent(frame.area, 0, 0, 0);
        size_t bytes;
        if (SUCCEEDED(hr)) hr = SharedResource::size(frame.area, bytes);
        if (FAILED(hr)) return hr;
        frame.pixels.assign(bytes, 0);
        UINT width = frame.area.right - frame.area.left, height = frame.area.bottom - frame.area.top;
        return bytes ? tree->render(frame.pixels, width, height, -frame.area.left, -frame.area.top,
                                   {0, 0, static_cast<LONG>(width), static_cast<LONG>(height)}, 0) : S_OK;
    }
    HRESULT publish(SharedFrame &frame)
    {
        bool same = published && EqualRect(&frame.area, &published_frame.area) &&
                    frame.pixels == published_frame.pixels && frame.inputs.size() == published_frame.inputs.size();
        if (same)
            for (size_t i = 0; i < frame.inputs.size(); ++i)
            {
                const auto &a = frame.inputs[i], &b = published_frame.inputs[i];
                if (a.area.left != b.area.left || a.area.top != b.area.top ||
                    a.area.right != b.area.right || a.area.bottom != b.area.bottom || !a.sink->same(*b.sink.p))
                { same = false; break; }
            }
        if (same) return S_FALSE; // unchanged relay chains must reach a fixed point
        HRESULT hr = resource->write(publisher, frame);
        if (SUCCEEDED(hr))
        {
            published_frame = std::move(frame);
            published = true;
            dcomp_shared_resource_published(resource.p, device);
        }
        return hr;
    }
    size_t published_regions() const { return published_frame.inputs.size(); }
    HRESULT validate_input() const { return root.p ? root->validate_input() : S_OK; }
};

// The embedded presenter retains a frame on the target HWND in Win32u, without
// adding an HWND. It follows parent geometry/visibility between commits.
// The older owned popup remains an explicit diagnostic path for topmost targets.
// A builtin class avoids retaining DLL callbacks in the window manager.
class Target final : public IDCompositionTarget
{
    LONG refs = 1;

  public:
    Device *device;
    HWND parent, overlay = nullptr;
    DWORD thread;
    bool embedded;
    CompositionCall composition_call_ptr = nullptr;
    Ref<InputScene> input_scene;
    Ref<Visual> root, committed_root;
    bool committed = false;
    Target(Device *owner, HWND hwnd, bool child) : device(owner), parent(hwnd), thread(GetCurrentThreadId()), embedded(child)
    {
        device->AddRef();
        InterlockedIncrement(&objects);
    }
    ~Target()
    {
        {
            Lock lock(device->mutex);
            erase(device->targets, this);
            root.reset();
        }
        if (input_scene.p) InputScene::bind(parent, reinterpret_cast<ULONG_PTR>(this), nullptr);
        if (embedded && composition_call_ptr)
            composition(composition_detach);
        if (overlay && IsWindow(overlay))
        {
            if (thread == GetCurrentThreadId())
                DestroyWindow(overlay);
            else
                PostMessageW(overlay, WM_CLOSE, 0, 0);
        }
        device->Release();
        InterlockedDecrement(&objects);
    }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void **out) override
    {
        if (!out)
            return E_POINTER;
        *out = nullptr;
        if (iid != __uuidof(IUnknown) && iid != target_iid)
            return E_NOINTERFACE;
        *out = static_cast<IDCompositionTarget *>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override
    {
        return InterlockedIncrement(&refs);
    }
    ULONG STDMETHODCALLTYPE Release() override
    {
        ULONG value = InterlockedDecrement(&refs);
        if (!value)
            delete this;
        return value;
    }
    HRESULT composition(UINT operation, UINT width = 0, UINT height = 0, const void *pixels = nullptr)
    {
        if (!composition_call_ptr)
            return E_NOTIMPL;
        CompositionFrame frame{composition_version, operation, reinterpret_cast<ULONG_PTR>(this), width, height, pixels};
        return composition_call_ptr(parent, reinterpret_cast<ULONG_PTR>(&frame), composition_call) ? S_OK : winerror();
    }
    HRESULT STDMETHODCALLTYPE SetRoot(IDCompositionVisual *value) override
    {
        Lock lock(device->mutex);
        Visual *visual = lookup(device->visuals, value);
        if (value && !visual)
            return E_INVALIDARG;
        root.retain(visual);
        return S_OK;
    }
    HRESULT prepare_input(InputScene **out, Visual *tree)
    {
        *out = nullptr;
        RECT rect;
        if (!GetClientRect(parent, &rect)) return winerror();
        std::vector<InputRegion> regions;
        HRESULT hr = tree ? tree->input_regions(regions, 0, 0,
            {0, 0, double(rect.right), double(rect.bottom)}) : S_OK;
        return FAILED(hr) ? hr : InputScene::create(regions, out);
    }
    HRESULT prepare(UINT &width, UINT &height, std::vector<BYTE> &pixels, Visual *tree)
    {
        if (thread != GetCurrentThreadId())
            return RPC_E_WRONG_THREAD;
        RECT rect;
        if (!IsWindow(parent) || (!embedded && !IsWindow(overlay)) || !GetClientRect(parent, &rect))
            return HRESULT_FROM_WIN32(ERROR_INVALID_WINDOW_HANDLE);
        if (embedded)
        {
            HRESULT hr = composition(composition_validate);
            if (FAILED(hr)) return hr;
        }
        width = rect.right;
        height = rect.bottom;
        if (!width || !height)
        {
            pixels.clear();
            return S_OK;
        }
        if (width > 16384 || height > 16384)
            return E_INVALIDARG;
        pixels.assign(static_cast<size_t>(width) * height * 4, 0);
        return tree ? tree->render(pixels, width, height, 0, 0, rect, 0) : S_OK;
    }
    HRESULT present(UINT width, UINT height, const std::vector<BYTE> &pixels)
    {
        if (embedded)
            return composition(composition_update, width, height, pixels.empty() ? nullptr : pixels.data());
        if (!width || !height || !IsWindowVisible(parent) || IsIconic(parent))
        {
            ShowWindow(overlay, SW_HIDE);
            return S_OK;
        }
        bool visible = false;
        for (size_t i = 3; i < pixels.size(); i += 4)
            if (pixels[i])
            {
                visible = true;
                break;
            }
        // Hide an entirely transparent popup. Wine/X11 otherwise leaves the
        // previous host frame visible when its layered shape becomes empty.
        if (!visible)
        {
            ShowWindow(overlay, SW_HIDE);
            return S_OK;
        }
        BITMAPINFO info = {};
        info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
        info.bmiHeader.biWidth = width;
        info.bmiHeader.biHeight = -static_cast<LONG>(height);
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        info.bmiHeader.biCompression = BI_RGB;
        void *bits = nullptr;
        HDC dc = CreateCompatibleDC(nullptr);
        if (!dc)
            return winerror();
        HBITMAP bitmap = CreateDIBSection(dc, &info, DIB_RGB_COLORS, &bits, nullptr, 0);
        if (!bitmap)
        {
            HRESULT hr = winerror();
            DeleteDC(dc);
            return hr;
        }
        memcpy(bits, pixels.data(), pixels.size());
        HGDIOBJ old = SelectObject(dc, bitmap);
        if (!old || old == HGDI_ERROR)
        {
            HRESULT hr = winerror();
            DeleteObject(bitmap);
            DeleteDC(dc);
            return hr;
        }
        POINT source = {}, position = {};
        if (!ClientToScreen(parent, &position))
        {
            HRESULT hr = winerror();
            SelectObject(dc, old);
            DeleteObject(bitmap);
            DeleteDC(dc);
            return hr;
        }
        SIZE size = {static_cast<LONG>(width), static_cast<LONG>(height)};
        BLENDFUNCTION blend = {AC_SRC_OVER, 0, 255, AC_SRC_ALPHA};
        BOOL ok = UpdateLayeredWindow(overlay, nullptr, &position, &size,
                                      dc, &source, 0, &blend, ULW_ALPHA);
        HRESULT hr = ok ? S_OK : winerror();
        SelectObject(dc, old);
        DeleteObject(bitmap);
        DeleteDC(dc);
        if (ok)
            ShowWindow(overlay, SW_SHOWNOACTIVATE);
        return hr;
    }
};

bool Device::has_imported(SharedResource *resource)
{
    // Called with mutex held; pending graphs never subscribe to refreshes.
    for (auto *target : targets)
        if (target->committed_root.p && target->committed_root->contains_resource(resource, 0)) return true;
    for (auto *target : shared_targets)
        if (target->committed_root.p && target->committed_root->contains_resource(resource, 0)) return true;
    return false;
}

void Device::dispatch_published_refresh()
{
    Lock lock(mutex);
    if (!has_imported(nullptr)) return;
    HRESULT hr = submit(true);
    if (GetEnvironmentVariableA("GHUB_DCOMP_TRACE", nullptr, 0))
        std::fprintf(stderr, "dcomp shared refresh: device=%p thread=%lu hr=%08lx\n",
                     this, GetCurrentThreadId(), static_cast<unsigned long>(hr));
}

LRESULT CALLBACK Device::refresh_window_proc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam)
{
    if (message == WM_NCCREATE) InterlockedIncrement(&objects);
    if (message == shared_refresh_message)
    {
        Ref<Device> device;
        {
            Lock lock(registry_mutex);
            for (auto *candidate : registry)
                if (candidate->refresh_window == hwnd)
                {
                    candidate->refresh_queued = false;
                    device.retain(candidate);
                    break;
                }
        }
        if (device.p) device->dispatch_published_refresh();
        return 0;
    }
    if (message == WM_NCDESTROY)
    {
        Lock lock(registry_mutex);
        for (auto *candidate : registry)
            if (candidate->refresh_window == hwnd)
            { candidate->refresh_window = nullptr; candidate->refresh_queued = false; }
        InterlockedDecrement(&objects);
    }
    return DefWindowProcW(hwnd, message, wparam, lparam);
}

void dcomp_shared_resource_published(SharedResource *, Device *publisher)
{
    // Never enter another device lock while the producer is locked. Posted
    // notifications contain no raw object pointer and hold no COM references.
    // The owning thread resolves the endpoint against the live registry.
    Lock lock(Device::registry_mutex);
    for (auto *device : Device::registry)
    {
        if (device == publisher || device->refresh_queued || !device->refresh_window) continue;
        if (PostMessageW(device->refresh_window, shared_refresh_message, 0, 0))
            device->refresh_queued = true;
        else if (GetEnvironmentVariableA("GHUB_DCOMP_TRACE", nullptr, 0))
            std::fprintf(stderr, "dcomp shared refresh: post failed hr=%08lx\n", static_cast<ULONG>(winerror()));
    }
}

class SurfaceFactory final : public IDCompositionSurfaceFactory
{
    LONG refs = 1;

  public:
    Device *device;
    Renderer renderer;
    explicit SurfaceFactory(Device *owner) : device(owner)
    {
        device->AddRef();
        InterlockedIncrement(&objects);
    }
    ~SurfaceFactory()
    {
        device->Release();
        InterlockedDecrement(&objects);
    }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void **out) override
    {
        if (!out)
            return E_POINTER;
        *out = nullptr;
        if (iid != __uuidof(IUnknown) && iid != __uuidof(IDCompositionSurfaceFactory))
            return E_NOINTERFACE;
        *out = static_cast<IDCompositionSurfaceFactory *>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override
    {
        return InterlockedIncrement(&refs);
    }
    ULONG STDMETHODCALLTYPE Release() override
    {
        ULONG value = InterlockedDecrement(&refs);
        if (!value)
            delete this;
        return value;
    }
    HRESULT STDMETHODCALLTYPE CreateSurface(UINT w, UINT h, DXGI_FORMAT format, DXGI_ALPHA_MODE alpha,
                                            IDCompositionSurface **out) override
    {
        Lock lock(device->mutex);
        return make_surface(device, renderer, w, h, format, alpha, out);
    }
    HRESULT STDMETHODCALLTYPE CreateVirtualSurface(UINT, UINT, DXGI_FORMAT, DXGI_ALPHA_MODE,
                                                   IDCompositionVirtualSurface **out) override
    {
        return unsupported_out(out, "factory virtual surface");
    }
};

// Only inspected private methods have a callable ABI. Other private slots
// terminate the experiment explicitly instead of masquerading as successful.
static HRESULT WINAPI private_qi(PrivateInterface *self, REFIID iid, void **out)
{
    return self->owner->QueryInterface(iid, out);
}
static ULONG WINAPI private_addref(PrivateInterface *self)
{
    return self->owner->AddRef();
}
static ULONG WINAPI private_release(PrivateInterface *self)
{
    return self->owner->Release();
}
static HRESULT WINAPI private_current(PrivateInterface *self, DWORD *out)
{
    if (!out)
        return E_INVALIDARG;
    Lock lock(self->owner->mutex);
    *out = self->owner->submitted;
    return S_OK;
}
static HRESULT WINAPI private_confirmed(PrivateInterface *self, DWORD *out)
{
    if (!out)
        return E_INVALIDARG;
    Lock lock(self->owner->mutex);
    *out = self->owner->confirmed;
    return S_OK;
}
static HRESULT WINAPI private_event(PrivateInterface *self, HANDLE event)
{
    return self->owner->set_event(event);
}
static HRESULT WINAPI private_open_shared(PrivateInterface *self, HANDLE handle, REFIID iid, void **out)
{
    return self->owner->open_shared(handle, iid, out);
}
static HRESULT WINAPI visual_layout_qi(VisualLayoutInterface *self, REFIID iid, void **out)
{
    return self->owner->QueryInterface(iid, out);
}
static ULONG WINAPI visual_layout_addref(VisualLayoutInterface *self)
{
    return self->owner->AddRef();
}
static ULONG WINAPI visual_layout_release(VisualLayoutInterface *self)
{
    return self->owner->Release();
}
static HRESULT WINAPI surface_partner_qi(SurfaceInterface *self, REFIID iid, void **out)
{
    return self->owner->QueryInterface(iid, out);
}
static ULONG WINAPI surface_partner_addref(SurfaceInterface *self) { return self->owner->AddRef(); }
static ULONG WINAPI surface_partner_release(SurfaceInterface *self) { return self->owner->Release(); }
static HRESULT WINAPI surface_partner_begin(SurfaceInterface *self, const RECT *rect, REFIID iid,
                                            void **out, POINT *offset)
{
    return self->owner->BeginDraw(rect, iid, out, offset);
}
static HRESULT WINAPI surface_partner_end(SurfaceInterface *self) { return self->owner->EndDraw(); }
static HRESULT WINAPI surface_partner_suspend(SurfaceInterface *self) { return self->owner->SuspendDraw(); }
static HRESULT WINAPI surface_partner_resume(SurfaceInterface *self) { return self->owner->ResumeDraw(); }
static HRESULT WINAPI surface_partner_scroll(SurfaceInterface *self, const RECT *scroll, const RECT *clip,
                                             int x, int y)
{
    return self->owner->Scroll(scroll, clip, x, y);
}
static HRESULT WINAPI surface_partner_resize(SurfaceInterface *self, UINT w, UINT h)
{
    return self->owner->resize(w, h);
}
static HRESULT WINAPI visual_layout_input(VisualLayoutInterface *self, HANDLE value)
{
    return self->owner->set_input(value);
}
template <unsigned Slot> static HRESULT WINAPI visual_layout_set(VisualLayoutInterface *self, float value)
{
    return self->owner->set_layout(Slot, value);
}
template <unsigned Slot> static HRESULT WINAPI private_missing(PrivateInterface *)
{
    ULONG_PTR slot = Slot;
    std::fprintf(stderr, "dcomp_experiment: unimplemented private slot %u\n", Slot);
    RaiseException(0xc0000002, EXCEPTION_NONCONTINUABLE, 1, &slot);
    return E_NOTIMPL;
}
template <size_t... I> static void fill_private(void **table, std::index_sequence<I...>)
{
    ((table[I] = reinterpret_cast<void *>(&private_missing<I>)), ...);
}
static void *rs5_table[57], *vb_table[72], *event_table[13], *shared_table[57];
static INIT_ONCE tables_once = INIT_ONCE_STATIC_INIT;
static BOOL CALLBACK init_tables(INIT_ONCE *, void *, void **)
{
    fill_private(rs5_table, std::make_index_sequence<57>{});
    fill_private(vb_table, std::make_index_sequence<72>{});
    fill_private(event_table, std::make_index_sequence<13>{});
    fill_private(shared_table, std::make_index_sequence<57>{});
    fill_private(visual_layout_table, std::make_index_sequence<52>{});
    fill_private(surface_partner_table, std::make_index_sequence<16>{});
    for (void **table : {rs5_table, vb_table, event_table, shared_table})
    {
        table[0] = reinterpret_cast<void *>(&private_qi);
        table[1] = reinterpret_cast<void *>(&private_addref);
        table[2] = reinterpret_cast<void *>(&private_release);
    }
    rs5_table[55] = vb_table[45] = reinterpret_cast<void *>(&private_current);
    rs5_table[56] = vb_table[46] = reinterpret_cast<void *>(&private_confirmed);
    // G HUB's rs5/vb internal-device layout: FrameworkUdk wrapper 32320
    // dispatches thunk 2b2e0 (+40 = slot8). The newer Windows reference
    // DLL uses slot5 under the same undocumented IID; that layout is NOT
    // compatible. Do not alias slot5 (a different legacy method) to this one.
    event_table[8] = reinterpret_cast<void *>(&private_event);
    // Bundled FrameworkUdk: QI d14b6158 at RVA27d80, thunk2b400 -> +e8.
    shared_table[29] = reinterpret_cast<void *>(&private_open_shared);
    // FrameworkUdk platform323c0 / 32640 use slots40/41 and36/37. The native
    // visual-restricted vtable RVA19dc20 also maps relative offsets to38/39.
    visual_layout_table[0] = reinterpret_cast<void *>(&visual_layout_qi);
    visual_layout_table[1] = reinterpret_cast<void *>(&visual_layout_addref);
    visual_layout_table[2] = reinterpret_cast<void *>(&visual_layout_release);
    visual_layout_table[35] = reinterpret_cast<void *>(&visual_layout_input);
    visual_layout_table[36] = reinterpret_cast<void *>(&visual_layout_set<36>);
    visual_layout_table[37] = reinterpret_cast<void *>(&visual_layout_set<37>);
    visual_layout_table[38] = reinterpret_cast<void *>(&visual_layout_set<38>);
    visual_layout_table[39] = reinterpret_cast<void *>(&visual_layout_set<39>);
    visual_layout_table[40] = reinterpret_cast<void *>(&visual_layout_set<40>);
    visual_layout_table[41] = reinterpret_cast<void *>(&visual_layout_set<41>);
    surface_partner_table[0] = reinterpret_cast<void *>(&surface_partner_qi);
    surface_partner_table[1] = reinterpret_cast<void *>(&surface_partner_addref);
    surface_partner_table[2] = reinterpret_cast<void *>(&surface_partner_release);
    surface_partner_table[3] = reinterpret_cast<void *>(&surface_partner_begin);
    surface_partner_table[4] = reinterpret_cast<void *>(&surface_partner_end);
    surface_partner_table[5] = reinterpret_cast<void *>(&surface_partner_suspend);
    surface_partner_table[6] = reinterpret_cast<void *>(&surface_partner_resume);
    surface_partner_table[7] = reinterpret_cast<void *>(&surface_partner_scroll);
    // FrameworkUdk 28350 queries IID5680c979; 31e00 -> 2b440 calls slot10.
    // Wine advertises UniversalApiContract10. Contract12+ uses slot11 under
    // the SAME IID, with a different slot10 ABI: do not alias the two layouts.
    surface_partner_table[10] = reinterpret_cast<void *>(&surface_partner_resize);
    return TRUE;
}
Device::Device() : owner_thread(GetCurrentThreadId())
{
    InitializeCriticalSection(&mutex);
    InitOnceExecuteOnce(&tables_once, init_tables, nullptr, nullptr);
    rs5 = {rs5_table, this};
    vb = {vb_table, this};
    events = {event_table, this};
    shared = {shared_table, this};
    register_device(this);
    HRESULT hr = refresh_initialization = create_refresh_window();
    if (FAILED(hr) && GetEnvironmentVariableA("GHUB_DCOMP_TRACE", nullptr, 0))
        std::fprintf(stderr, "dcomp shared refresh: window creation failed hr=%08lx\n",
                     static_cast<unsigned long>(hr));
    InterlockedIncrement(&objects);
}
Device::~Device()
{
    HWND window;
    {
        Lock lock(registry_mutex);
        window = refresh_window;
        refresh_window = nullptr;
    }
    if (window)
    {
        if (owner_thread == GetCurrentThreadId())
            DestroyWindow(window);
        else
        {
            // The registry already dropped this endpoint on final Release.
            // Any queued refresh now resolves to nothing. Window lifetime
            // keeps DllCanUnloadNow false until its owner destroys it.
            PostMessageW(window, WM_CLOSE, 0, 0);
        }
    }
    if (completion)
        CloseHandle(completion);
    DeleteCriticalSection(&mutex);
    InterlockedDecrement(&objects);
}
HRESULT Device::QueryInterface(REFIID iid, void **out)
{
    if (!out)
        return E_POINTER;
    *out = nullptr;
    if (iid == __uuidof(IUnknown) || iid == __uuidof(IDCompositionDevice2) ||
        iid == __uuidof(IDCompositionDesktopDevice))
        *out = static_cast<IDCompositionDesktopDevice *>(this);
    else if (iid == device1_iid)
        *out = static_cast<IDCompositionDevice *>(this);
    else if (iid == partner_rs5)
        *out = &rs5;
    else if (iid == partner_vb)
        *out = &vb;
    else if (iid == restricted)
        *out = &events;
    else if (iid == desktop_partner)
        *out = &shared;
    else
    {
        std::fprintf(stderr,
                     "dcomp_experiment: unsupported device IID {%08lx-%04x-%04x-%02x%02x-%02x%02x%02x%02x%02x%02x}\n",
                     iid.Data1, iid.Data2, iid.Data3, iid.Data4[0], iid.Data4[1], iid.Data4[2], iid.Data4[3],
                     iid.Data4[4], iid.Data4[5], iid.Data4[6], iid.Data4[7]);
        return E_NOINTERFACE;
    }
    AddRef();
    return S_OK;
}
HRESULT Device::set_event(HANDLE event)
{
    HANDLE duplicate = nullptr;
    if (event &&
        !DuplicateHandle(GetCurrentProcess(), event, GetCurrentProcess(), &duplicate, EVENT_MODIFY_STATE, FALSE, 0))
        return winerror();
    if (duplicate)
    {
        // Query the duplicated handle so caller-side close/reuse cannot race us.
        // Neither waiting nor resetting is a valid type check: both can consume
        // an auto-reset event's signal. NtQueryObject requires no query access.
        using QueryObject = NTSTATUS(NTAPI *)(HANDLE, OBJECT_INFORMATION_CLASS, void *, ULONG, ULONG *);
        auto query = reinterpret_cast<QueryObject>(
            reinterpret_cast<void *>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "NtQueryObject")));
        alignas(void *) BYTE storage[1024] = {};
        ULONG used = 0;
        NTSTATUS status = query ? query(duplicate, ObjectTypeInformation, storage, sizeof(storage), &used) : -1;
        const auto *name = reinterpret_cast<const UNICODE_STRING *>(storage);
        if (status < 0 || name->Length != 5 * sizeof(WCHAR) || !name->Buffer ||
            memcmp(name->Buffer, L"Event", name->Length))
        {
            CloseHandle(duplicate);
            return HRESULT_FROM_WIN32(ERROR_INVALID_HANDLE);
        }
    }
    Lock lock(mutex);
    if (completion)
        CloseHandle(completion);
    completion = duplicate;
    return S_OK;
}
HRESULT Device::CreateVisual(IDCompositionVisual2 **out)
{
    if (!out)
        return E_POINTER;
    *out = nullptr;
    Lock lock(mutex);
    try
    {
        Ref<Visual> visual;
        visual.p = new Visual(this);
        visuals.push_back(visual.p);
        *out = visual.detach();
        return S_OK;
    }
    catch (const std::bad_alloc &)
    {
        return E_OUTOFMEMORY;
    }
}
HRESULT Device::CreateVisual(IDCompositionVisual **out)
{
    if (!out)
        return E_POINTER;
    *out = nullptr;
    Ref<IDCompositionVisual2> visual;
    HRESULT hr = CreateVisual(&visual.p);
    if (SUCCEEDED(hr))
        *out = static_cast<IDCompositionVisual *>(visual.detach());
    return hr;
}
HRESULT Device::CreateSurfaceFactory(IUnknown *input, IDCompositionSurfaceFactory **out)
{
    if (!out)
        return E_POINTER;
    *out = nullptr;
    if (!input)
        return E_INVALIDARG;
    try
    {
        Ref<SurfaceFactory> factory;
        factory.p = new SurfaceFactory(this);
        HRESULT hr = factory->renderer.initialize(input);
        if (FAILED(hr))
            return hr;
        *out = factory.detach();
        return S_OK;
    }
    catch (const std::bad_alloc &)
    {
        return E_OUTOFMEMORY;
    }
}
HRESULT Device::CreateSurface(UINT w, UINT h, DXGI_FORMAT format, DXGI_ALPHA_MODE alpha, IDCompositionSurface **out)
{
    Lock lock(mutex);
    return make_surface(this, renderer, w, h, format, alpha, out);
}
HRESULT Device::CreateTargetForHwnd(HWND hwnd, BOOL topmost, IDCompositionTarget **out)
{
    if (GetEnvironmentVariableA("GHUB_DCOMP_TRACE", nullptr, 0))
    {
        char name[128] = {};
        RECT window = {}, client = {};
        GetClassNameA(hwnd, name, sizeof(name));
        GetWindowRect(hwnd, &window);
        GetClientRect(hwnd, &client);
        std::fprintf(stderr, "dcomp HWND target: hwnd=%p parent=%p root=%p class=%s topmost=%d "
                             "style=%08lx exstyle=%08lx window=(%ld,%ld,%ld,%ld) client=(%ld,%ld)\n",
                     hwnd, GetParent(hwnd), GetAncestor(hwnd, GA_ROOT), name, topmost,
                     static_cast<unsigned long>(GetWindowLongW(hwnd, GWL_STYLE)),
                     static_cast<unsigned long>(GetWindowLongW(hwnd, GWL_EXSTYLE)),
                     window.left, window.top, window.right, window.bottom, client.right, client.bottom);
    }
    if (!out)
        return E_POINTER;
    *out = nullptr;
    DWORD pid = 0, thread = GetWindowThreadProcessId(hwnd, &pid);
    if (!thread || pid != GetCurrentProcessId())
        return E_INVALIDARG;
    if (thread != GetCurrentThreadId())
        return RPC_E_WRONG_THREAD;
    WCHAR opt_in[2];
    bool embedded = GetEnvironmentVariableW(L"GHUB_DCOMP_CHILD_TARGET", opt_in, 2) == 1 && opt_in[0] == L'1';
    if (embedded)
    {
        if (topmost)
            return unsupported("above-child embedded target");
        if (GetEnvironmentVariableW(L"WINE_GHUB_CHILD_COMPOSITION", opt_in, 2) != 1 || opt_in[0] != L'1')
            return unsupported("child target requires matched Win32u child-composition build");
        if (GetWindowLongW(GetAncestor(hwnd, GA_ROOT), GWL_EXSTYLE) & WS_EX_LAYERED)
            return unsupported("embedded target in a layered root window");
    }
    else
    {
        if (!topmost)
            return unsupported("target below child windows requires GHUB_DCOMP_CHILD_TARGET=1");
        if (GetEnvironmentVariableW(L"GHUB_DCOMP_POPUP_TARGET", opt_in, 2) != 1 || opt_in[0] != L'1')
            return unsupported("embedded HWND target (lab popup requires GHUB_DCOMP_POPUP_TARGET=1)");
        if (GetAncestor(hwnd, GA_ROOT) != hwnd)
            return unsupported("child HWND target");
    }
    Lock lock(mutex);
    for (auto *target : targets)
        if (target->parent == hwnd)
            return E_INVALIDARG;
    try
    {
        Ref<Target> target;
        target.p = new Target(this, hwnd, embedded);
        if (embedded)
        {
            HMODULE win32u = GetModuleHandleW(L"win32u.dll");
            target->composition_call_ptr = win32u ? reinterpret_cast<CompositionCall>(
                reinterpret_cast<void *>(GetProcAddress(win32u, "NtUserCallHwndParam"))) : nullptr;
            HRESULT hr = target->composition(composition_attach);
            if (FAILED(hr)) return hr;
        }
        else
        {
            target->overlay = CreateWindowExW(WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_NOPARENTNOTIFY,
                                          L"STATIC", L"G HUB lab composition", WS_POPUP,
                                          0, 0, 1, 1, hwnd, nullptr,
                                          GetModuleHandleW(nullptr), nullptr);
            if (!target->overlay) return winerror();
        }
        targets.push_back(target.p);
        *out = target.detach();
        return S_OK;
    }
    catch (const std::bad_alloc &)
    {
        return E_OUTOFMEMORY;
    }
}
HRESULT Device::Commit()
{
    return submit(false);
}

HRESULT Device::submit(bool refresh)
{
    Lock lock(mutex);
    const bool trace_commit = GetEnvironmentVariableA("GHUB_DCOMP_TRACE", nullptr, 0) != 0;
    if (trace_commit) std::fprintf(stderr, "dcomp %s begin: device=%p windows=%zu shared=%zu\n",
                                  refresh ? "refresh" : "Commit", this, targets.size(), shared_targets.size());
    struct Frame
    {
        Target *target = nullptr;
        UINT width = 0, height = 0;
        std::vector<BYTE> pixels;
        Ref<InputScene> input;
        Ref<Visual> tree;
    };
    try
    {
        // Freeze application state only for an explicit Commit. Automatic
        // refresh copies the last accepted graph (including surface pixels,
        // tree order, input bindings and target root), then samples imports.
        size_t budget = 256u * 1024u * 1024u;
        auto capture = [&](Visual *root, Ref<Visual> &tree) -> HRESULT
        {
            if (!root) return S_OK;
            HRESULT hr = root->freeze(&tree.p, budget);
            if (SUCCEEDED(hr)) hr = tree->validate_input();
            if (SUCCEEDED(hr)) hr = tree->sample_imports();
            return hr;
        };
        std::vector<SharedTarget *> shared_list;
        std::vector<Target *> window_list;
        for (auto *target : shared_targets)
            if (!refresh || target->committed) shared_list.push_back(target);
        for (auto *target : targets)
            if (!refresh || target->committed) window_list.push_back(target);
        std::vector<Ref<Visual>> shared_trees(shared_list.size());
        std::vector<SharedFrame> shared_frames(shared_list.size());
        for (size_t i = 0; i < shared_list.size(); ++i)
        {
            auto *target = shared_list[i];
            HRESULT hr = capture(refresh ? target->committed_root.p : target->pending_root(), shared_trees[i]);
            if (SUCCEEDED(hr)) hr = target->prepare(shared_frames[i], shared_trees[i].p);
            if (FAILED(hr)) return hr;
        }
        std::vector<Frame> frames(window_list.size());
        for (size_t i = 0; i < window_list.size(); ++i)
        {
            auto &frame = frames[i];
            frame.target = window_list[i];
            HRESULT hr = capture(refresh ? frame.target->committed_root.p : frame.target->root.p, frame.tree);
            if (SUCCEEDED(hr)) hr = frame.target->prepare_input(&frame.input.p, frame.tree.p);
            if (SUCCEEDED(hr)) hr = frame.target->prepare(frame.width, frame.height, frame.pixels, frame.tree.p);
            if (FAILED(hr)) return hr;
        }
        // No cross-device calls during publication. Consumers receive queued
        // notifications after the producer lock has been released.
        for (size_t i = 0; i < shared_list.size(); ++i)
        {
            HRESULT hr = shared_list[i]->publish(shared_frames[i]);
            if (FAILED(hr)) return hr;
        }
        for (auto &frame : frames)
        {
            // A destination on the producer device sees its current batch.
            HRESULT hr = frame.tree.p ? frame.tree->sample_imports() : S_OK;
            frame.input.reset();
            if (SUCCEEDED(hr)) hr = frame.target->prepare_input(&frame.input.p, frame.tree.p);
            if (SUCCEEDED(hr)) hr = frame.target->prepare(frame.width, frame.height, frame.pixels, frame.tree.p);
            if (FAILED(hr)) return hr;
            hr = InputScene::bind(frame.target->parent, reinterpret_cast<ULONG_PTR>(frame.target), frame.input.p);
            if (FAILED(hr)) return hr;
            hr = frame.target->present(frame.width, frame.height, frame.pixels);
            if (FAILED(hr))
            {
                InputScene::bind(frame.target->parent, reinterpret_cast<ULONG_PTR>(frame.target), frame.target->input_scene.p);
                return hr;
            }
            frame.target->input_scene.retain(frame.input.p);
        }
        if (!GdiFlush()) return winerror();
        for (size_t i = 0; i < shared_list.size(); ++i)
        {
            shared_list[i]->committed_root = std::move(shared_trees[i]);
            shared_list[i]->committed = true;
        }
        for (auto &frame : frames)
        {
            frame.target->committed_root = std::move(frame.tree);
            frame.target->committed = true;
        }
        if (!refresh)
        {
            for (auto *visual : visuals)
                visual->committed_input.retain(visual->pending_input.p);
            // Refresh is not an application batch: do not advance commit IDs
            // or signal its completion event for another device's publication.
            ++submitted;
            confirmed = submitted;
            if (completion && !SetEvent(completion)) return winerror();
        }
        if (trace_commit)
        {
            size_t regions = 0, bound = 0;
            for (const auto *target : shared_list) regions += target->published_regions();
            for (const auto &frame : frames) if (frame.input.p) ++bound;
            std::fprintf(stderr, "dcomp %s accepted: device=%p shared_regions=%zu bound_windows=%zu\n",
                         refresh ? "refresh" : "Commit", this, regions, bound);
        }
        return S_OK;
    }
    catch (const std::bad_alloc &) { return E_OUTOFMEMORY; }
}

HRESULT Device::open_shared(HANDLE handle, REFIID iid, void **out)
{
    if (GetEnvironmentVariableA("GHUB_DCOMP_TRACE", nullptr, 0))
        std::fprintf(stderr, "dcomp shared open: handle=%p IID=%08lx\n", handle, iid.Data1);
    if (!out) return E_POINTER;
    *out = nullptr;
    if (iid != visual_iid && iid != __uuidof(IDCompositionVisual2) && iid != target_iid)
        return E_NOINTERFACE;
    try
    {
        Ref<SharedResource> resource;
        HRESULT hr = SharedResource::open(handle, &resource.p);
        if (FAILED(hr)) return hr;
        Lock lock(mutex);
        if (iid == target_iid)
        {
            Ref<SharedTarget> target;
            target.p = new SharedTarget(resource.p);
            hr = target->initialize(this);
            return FAILED(hr) ? hr : target->QueryInterface(iid, out);
        }
        Ref<Visual> visual;
        visual.p = new Visual(this);
        visual->imported.retain(resource.p);
        visuals.push_back(visual.p);
        return visual->QueryInterface(iid, out);
    }
    catch (const std::bad_alloc &) { return E_OUTOFMEMORY; }
}

extern "C" HRESULT WINAPI DCompositionCreateDevice2(IUnknown *rendering, REFIID iid, void **out)
{
    if (!out)
        return E_POINTER;
    *out = nullptr;
    try
    {
        Ref<Device> device;
        device.p = new Device;
        HRESULT hr = device->refresh_initialization;
        if (SUCCEEDED(hr)) hr = device->renderer.initialize(rendering);
        return FAILED(hr) ? hr : device->QueryInterface(iid, out);
    }
    catch (const std::bad_alloc &)
    {
        return E_OUTOFMEMORY;
    }
}
extern "C" HRESULT WINAPI DCompositionCreateDevice3(IUnknown *rendering, REFIID iid, void **out)
{
    return DCompositionCreateDevice2(rendering, iid, out);
}
extern "C" HRESULT WINAPI DCompositionCreateDevice(IDXGIDevice *rendering, REFIID iid, void **out)
{
    return DCompositionCreateDevice2(rendering, iid, out);
}
extern "C" HRESULT WINAPI DllCanUnloadNow()
{
    return objects ? S_FALSE : S_OK;
}

// The user-mode relay has real shared storage and committed pixels, but is not
// an NtDComposition kernel object. Native consumers must open it through this
// DLL's implemented private partner ABI; other backends are unsupported.
extern "C" HRESULT WINAPI DCompositionCreateSharedVisualHandle(HANDLE *out)
{
    HRESULT hr = SharedResource::create(out);
    if (GetEnvironmentVariableA("GHUB_DCOMP_TRACE", nullptr, 0))
        std::fprintf(stderr, "dcomp shared create: hr=%08lx handle=%p\n", hr, out ? *out : nullptr);
    return hr;
}
extern "C" HRESULT WINAPI DCompositionCreateTargetForHandle(HANDLE handle, IDCompositionTarget **out)
{
    if (!out) return E_POINTER;
    *out = nullptr;
    try
    {
        Ref<SharedResource> resource;
        HRESULT hr = SharedResource::open(handle, &resource.p);
        if (FAILED(hr)) return hr;
        Ref<SharedTarget> target;
        target.p = new SharedTarget(resource.p);
        hr = target->initialize(nullptr);
        if (FAILED(hr)) return hr;
        *out = target.detach();
        return S_OK;
    }
    catch (const std::bad_alloc &) { return E_OUTOFMEMORY; }
}

// Window procedures belong to this DLL. Unregister the class when the last
// object/window is gone and the caller actually unloads the module.
extern "C" BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, void *)
{
    if (reason == DLL_PROCESS_DETACH) UnregisterClassW(shared_refresh_class, instance);
    return TRUE;
}
