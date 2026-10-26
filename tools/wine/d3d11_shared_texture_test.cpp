/* Real shared-texture pixels, cross-device/process lifetime and rejection tests.
 * Use the private D3D11/DXGI/WineD3D trio on private Xvfb, never the live display. */
#include <array>
#include <cstdio>
#include <cstdlib>
#include <d3d11_1.h>
#include <d3dcompiler.h>
#include <dxgi1_2.h>
#include <windows.h>

static unsigned checks, failures;
#define CHECK(x)                                                                                                       \
    do                                                                                                                 \
    {                                                                                                                  \
        ++checks;                                                                                                      \
        if (!(x))                                                                                                      \
        {                                                                                                              \
            ++failures;                                                                                                \
            std::printf("FAIL %d: %s\n", __LINE__, #x);                                                                \
        }                                                                                                              \
    } while (0)
#define HR(x)                                                                                                          \
    do                                                                                                                 \
    {                                                                                                                  \
        HRESULT h_ = (x);                                                                                              \
        CHECK(SUCCEEDED(h_));                                                                                          \
        if (FAILED(h_))                                                                                                \
        {                                                                                                              \
            std::printf("HRESULT %08lx: %s\n", h_, #x);                                                                \
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
    Ref() = default;
    Ref(const Ref &) = delete;
    Ref &operator=(const Ref &) = delete;
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
struct Handle
{
    HANDLE h = nullptr;
    ~Handle()
    {
        if (h)
            CloseHandle(h);
    }
};
constexpr UINT W = 12, H = 10, RED = 0xff0000ff, GREEN = 0xff00ff00, BLUE = 0xffff0000;
constexpr DWORD access_rw = DXGI_SHARED_RESOURCE_READ | DXGI_SHARED_RESOURCE_WRITE;
using Pixels = std::array<UINT, W * H>;
static D3D11_TEXTURE2D_DESC desc()
{
    D3D11_TEXTURE2D_DESC d = {};
    d.Width = W;
    d.Height = H;
    d.MipLevels = d.ArraySize = d.SampleDesc.Count = 1;
    d.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    d.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    d.MiscFlags = D3D11_RESOURCE_MISC_SHARED | D3D11_RESOURCE_MISC_SHARED_NTHANDLE;
    return d;
}
struct Device
{
    Ref<ID3D11Device> device;
    Ref<ID3D11Device1> v1;
    Ref<ID3D11DeviceContext> context;
    bool init()
    {
        HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                                       nullptr, 0, D3D11_SDK_VERSION, &device.p, nullptr, &context.p);
        CHECK(SUCCEEDED(hr));
        if (FAILED(hr))
            return false;
        hr = device->QueryInterface(__uuidof(ID3D11Device1), reinterpret_cast<void **>(&v1.p));
        CHECK(SUCCEEDED(hr));
        return SUCCEEDED(hr);
    }
    bool wait()
    {
        Ref<ID3D11Query> event;
        D3D11_QUERY_DESC q = {D3D11_QUERY_EVENT, 0};
        HRESULT hr = device->CreateQuery(&q, &event.p);
        if (FAILED(hr))
        {
            CHECK(false);
            return false;
        }
        context->End(event.p);
        context->Flush();
        DWORD start = GetTickCount();
        while ((hr = context->GetData(event.p, nullptr, 0, 0)) == S_FALSE && GetTickCount() - start < 10000)
            Sleep(1);
        CHECK(hr == S_OK);
        return hr == S_OK;
    }
    void verify(ID3D11Texture2D *texture, const Pixels &want, const char *name)
    {
        D3D11_TEXTURE2D_DESC d = desc();
        d.MiscFlags = d.BindFlags = 0;
        d.Usage = D3D11_USAGE_STAGING;
        d.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        Ref<ID3D11Texture2D> stage;
        HR(device->CreateTexture2D(&d, nullptr, &stage.p));
        context->CopyResource(stage.p, texture);
        D3D11_MAPPED_SUBRESOURCE map = {};
        HR(context->Map(stage.p, 0, D3D11_MAP_READ, 0, &map));
        unsigned bad = 0;
        for (UINT y = 0; y < H; ++y)
            for (UINT x = 0; x < W; ++x)
                if (reinterpret_cast<UINT *>(static_cast<BYTE *>(map.pData) + y * map.RowPitch)[x] != want[y * W + x])
                    ++bad;
        context->Unmap(stage.p, 0);
        std::printf("pixels %s: %u mismatches\n", name, bad);
        CHECK(!bad);
    }
};
static void open(Device &d, HANDLE h, Ref<ID3D11Texture2D> &texture)
{
    HR(d.v1->OpenSharedResource1(h, __uuidof(ID3D11Texture2D), reinterpret_cast<void **>(&texture.p)));
    D3D11_TEXTURE2D_DESC got = {};
    if (texture.p)
        texture->GetDesc(&got);
    CHECK(got.Width == W && got.Height == H && got.MiscFlags == desc().MiscFlags);
}
static void child(HANDLE h, bool race, HANDLE ready, HANDLE go)
{
    Device d;
    if (!d.init())
        return;
    Ref<ID3D11Texture2D> texture;
    open(d, h, texture);
    if (!texture.p)
        return;
    CHECK(CloseHandle(h));
    if (race)
    {
        CHECK(SetEvent(ready));
        CHECK(WaitForSingleObject(go, 10000) == WAIT_OBJECT_0);
        CloseHandle(ready);
        CloseHandle(go);
        D3D11_BOX box = {2, 2, 0, 3, 3, 1};
        for (UINT i = 0; i < 20; ++i)
        {
            UINT pixel = BLUE | i;
            d.context->UpdateSubresource(texture.p, 0, &box, &pixel, 4, 4);
            Sleep(1);
        }
        d.wait();
        return;
    }
    Pixels expected;
    expected.fill(GREEN);
    d.verify(texture.p, expected, "child reads parent");
    const UINT pixel = BLUE;
    D3D11_BOX box = {3, 4, 0, 4, 5, 1};
    d.context->UpdateSubresource(texture.p, 0, &box, &pixel, 4, 4);
    if (!d.wait())
        return;
    expected[4 * W + 3] = BLUE;
    d.verify(texture.p, expected, "child partial upload preserves parent pixels");
}
static void sampling(Device &d, ID3D11Texture2D *source, const Pixels &expected, bool deferred = false)
{
    const char *code = "Texture2D<float4> tex:register(t0);"
                       "float4 vs(uint id:SV_VertexID):SV_Position { return float4(id==2?3:-1,id==1?-3:1,0,1); }"
                       "float4 ps(float4 p:SV_Position):SV_Target { return tex.Load(int3(p.xy,0)); }";
    Ref<ID3DBlob> vsb, psb;
    Ref<ID3D11VertexShader> vs;
    Ref<ID3D11PixelShader> ps;
    HR(D3DCompile(code, lstrlenA(code), nullptr, nullptr, nullptr, "vs", "vs_4_0", 0, 0, &vsb.p, nullptr));
    HR(D3DCompile(code, lstrlenA(code), nullptr, nullptr, nullptr, "ps", "ps_4_0", 0, 0, &psb.p, nullptr));
    HR(d.device->CreateVertexShader(vsb->GetBufferPointer(), vsb->GetBufferSize(), nullptr, &vs.p));
    HR(d.device->CreatePixelShader(psb->GetBufferPointer(), psb->GetBufferSize(), nullptr, &ps.p));
    Ref<ID3D11Texture2D> output;
    auto td = desc();
    HR(d.device->CreateTexture2D(&td, nullptr, &output.p));
    Ref<IDXGIResource1> shared;
    HR(output->QueryInterface(__uuidof(IDXGIResource1), reinterpret_cast<void **>(&shared.p)));
    Handle exported;
    HR(shared->CreateSharedHandle(nullptr, access_rw, nullptr, &exported.h));
    Ref<ID3D11RenderTargetView> rt;
    Ref<ID3D11ShaderResourceView> srv;
    HR(d.device->CreateRenderTargetView(output.p, nullptr, &rt.p));
    HR(d.device->CreateShaderResourceView(source, nullptr, &srv.p));
    D3D11_VIEWPORT vp = {0, 0, W, H, 0, 1};
    Ref<ID3D11RasterizerState> rasterizer;
    D3D11_RASTERIZER_DESC rd = {};
    rd.FillMode = D3D11_FILL_SOLID;
    rd.CullMode = D3D11_CULL_NONE;
    rd.DepthClipEnable = TRUE;
    HR(d.device->CreateRasterizerState(&rd, &rasterizer.p));
    Ref<ID3D11DeviceContext> recorded;
    if (deferred)
        HR(d.device->CreateDeferredContext(0, &recorded.p));
    ID3D11DeviceContext *c = deferred ? recorded.p : d.context.p;
    c->RSSetState(rasterizer.p);
    c->RSSetViewports(1, &vp);
    c->OMSetRenderTargets(1, &rt.p, nullptr);
    c->VSSetShader(vs.p, nullptr, 0);
    c->PSSetShader(ps.p, nullptr, 0);
    c->PSSetShaderResources(0, 1, &srv.p);
    c->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    c->Draw(3, 0);
    if (deferred)
    {
        Ref<ID3D11CommandList> list;
        HR(recorded->FinishCommandList(FALSE, &list.p));
        d.context->ExecuteCommandList(list.p, FALSE);
    }
    if (!d.wait())
        return;
    Device consumer;
    if (!consumer.init())
        return;
    Ref<ID3D11Texture2D> imported;
    open(consumer, exported.h, imported);
    if (!imported.p)
        return;
    consumer.verify(imported.p, expected,
                    deferred ? "deferred shader shared output" : "shader shared input and output");
    d.context->ClearState();
}
static void run()
{
    Device a, b;
    if (!a.init() || !b.init())
        return;
    auto td = desc();
    Pixels expected;
    expected.fill(RED);
    D3D11_SUBRESOURCE_DATA initial = {expected.data(), W * 4, W * H * 4};
    Ref<ID3D11Texture2D> texture, opened;
    HR(a.device->CreateTexture2D(&td, &initial, &texture.p));
    Ref<IDXGIResource1> dxgi;
    HR(texture->QueryInterface(__uuidof(IDXGIResource1), reinterpret_cast<void **>(&dxgi.p)));
    Handle exported, duplicate;
    SECURITY_ATTRIBUTES sa = {sizeof(sa), nullptr, TRUE};
    HR(dxgi->CreateSharedHandle(&sa, access_rw, nullptr, &exported.h));
    DWORD flags = 0;
    CHECK(GetHandleInformation(exported.h, &flags) && (flags & HANDLE_FLAG_INHERIT));
    HANDLE again = nullptr;
    CHECK(dxgi->CreateSharedHandle(nullptr, access_rw, nullptr, &again) == DXGI_ERROR_INVALID_CALL && !again);
    CHECK(DuplicateHandle(GetCurrentProcess(), exported.h, GetCurrentProcess(), &duplicate.h, 0, TRUE,
                          DUPLICATE_SAME_ACCESS));
    open(b, duplicate.h, opened);
    if (!opened.p)
        return;
    b.verify(opened.p, expected, "initial data on another device");
    Ref<ID3D11RenderTargetView> rt;
    HR(b.device->CreateRenderTargetView(opened.p, nullptr, &rt.p));
    const float green[4] = {0, 1, 0, 1};
    b.context->ClearRenderTargetView(rt.p, green);
    if (!b.wait())
        return;
    expected.fill(GREEN);
    a.verify(texture.p, expected, "GPU clear visible to creator");
    sampling(a, texture.p, expected);
    char exe[MAX_PATH], command[2 * MAX_PATH];
    CHECK(GetModuleFileNameA(nullptr, exe, MAX_PATH));
    std::snprintf(command, sizeof(command), "\"%s\" --child %llu", exe,
                  static_cast<unsigned long long>(reinterpret_cast<ULONG_PTR>(duplicate.h)));
    STARTUPINFOA start = {};
    start.cb = sizeof(start);
    PROCESS_INFORMATION process = {};
    BOOL launched =
        CreateProcessA(nullptr, command, nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr, nullptr, &start, &process);
    CHECK(launched);
    if (launched)
    {
        CHECK(WaitForSingleObject(process.hProcess, 30000) == WAIT_OBJECT_0);
        DWORD status = 99;
        CHECK(GetExitCodeProcess(process.hProcess, &status) && status == 0);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        expected[4 * W + 3] = BLUE;
        a.verify(texture.p, expected, "child update visible in original device");
        sampling(b, opened.p, expected);
        sampling(a, texture.p, expected, true);
    }
    // Concurrent partial writes must merge through the shared transaction lock.
    Handle ready, go;
    ready.h = CreateEventW(&sa, TRUE, FALSE, nullptr);
    go.h = CreateEventW(&sa, TRUE, FALSE, nullptr);
    CHECK(ready.h && go.h);
    std::snprintf(command, sizeof(command), "\"%s\" --race %llu %llu %llu", exe,
                  static_cast<unsigned long long>(reinterpret_cast<ULONG_PTR>(duplicate.h)),
                  static_cast<unsigned long long>(reinterpret_cast<ULONG_PTR>(ready.h)),
                  static_cast<unsigned long long>(reinterpret_cast<ULONG_PTR>(go.h)));
    launched =
        CreateProcessA(nullptr, command, nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr, nullptr, &start, &process);
    CHECK(launched);
    if (launched)
    {
        CHECK(WaitForSingleObject(ready.h, 10000) == WAIT_OBJECT_0);
        CHECK(SetEvent(go.h));
        D3D11_BOX box = {1, 1, 0, 2, 2, 1};
        for (UINT i = 0; i < 20; ++i)
        {
            UINT pixel = RED | (i << 8);
            a.context->UpdateSubresource(texture.p, 0, &box, &pixel, 4, 4);
            Sleep(1);
        }
        if (!a.wait())
            return;
        CHECK(WaitForSingleObject(process.hProcess, 30000) == WAIT_OBJECT_0);
        DWORD status = 99;
        CHECK(GetExitCodeProcess(process.hProcess, &status) && !status);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        expected[W + 1] = RED | (19 << 8);
        expected[2 * W + 2] = BLUE | 19;
        b.verify(opened.p, expected, "concurrent processes preserve disjoint writes");
    }
    // Reopen from a duplicate after every original COM resource and handle dies.
    rt.reset();
    opened.reset();
    dxgi.reset();
    texture.reset();
    CHECK(CloseHandle(exported.h));
    exported.h = nullptr;
    open(b, duplicate.h, opened);
    if (!opened.p)
        return;
    CHECK(CloseHandle(duplicate.h));
    duplicate.h = nullptr;
    b.verify(opened.p, expected, "resource survives original release and handle closure");
    a.context->ClearState();
    b.context->ClearState();
}
static void errors_and_names()
{
    Device d;
    if (!d.init())
        return;
    auto td = desc();
    Ref<ID3D11Texture2D> texture, second, named;
    Ref<IDXGIResource1> r1, r2;
    HR(d.device->CreateTexture2D(&td, nullptr, &texture.p));
    HR(texture->QueryInterface(__uuidof(IDXGIResource1), reinterpret_cast<void **>(&r1.p)));
    CHECK(r1->CreateSharedHandle(nullptr, access_rw, nullptr, nullptr) == E_INVALIDARG);
    Handle handle;
    CHECK(r1->CreateSharedHandle(nullptr, DXGI_SHARED_RESOURCE_READ, nullptr, &handle.h) == E_NOTIMPL && !handle.h);
    WCHAR name[80];
    swprintf(name, 80, L"Local\\GHubSharedTest-%lu", GetCurrentProcessId());
    HR(r1->CreateSharedHandle(nullptr, access_rw, name, &handle.h));
    HR(d.v1->OpenSharedResourceByName(name, access_rw, __uuidof(ID3D11Texture2D), reinterpret_cast<void **>(&named.p)));
    HR(d.device->CreateTexture2D(&td, nullptr, &second.p));
    HR(second->QueryInterface(__uuidof(IDXGIResource1), reinterpret_cast<void **>(&r2.p)));
    HANDLE collision = nullptr;
    CHECK(r2->CreateSharedHandle(nullptr, access_rw, name, &collision) == DXGI_ERROR_NAME_ALREADY_EXISTS && !collision);
    // Rejection must leave the resource available for a later valid export.
    Handle unnamed;
    HR(r2->CreateSharedHandle(nullptr, access_rw, nullptr, &unnamed.h));
    void *bad = reinterpret_cast<void *>(1);
    Handle event;
    event.h = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    CHECK(FAILED(d.v1->OpenSharedResource1(event.h, __uuidof(ID3D11Texture2D), &bad)) && !bad);
    bad = reinterpret_cast<void *>(1);
    Handle readonly;
    CHECK(DuplicateHandle(GetCurrentProcess(), handle.h, GetCurrentProcess(), &readonly.h, SECTION_MAP_READ, FALSE, 0));
    CHECK(FAILED(d.v1->OpenSharedResource1(readonly.h, __uuidof(ID3D11Texture2D), &bad)) && !bad);
    named.reset();
    r1.reset();
    texture.reset();
    td.MiscFlags = 0;
    HR(d.device->CreateTexture2D(&td, nullptr, &texture.p));
    HR(texture->QueryInterface(__uuidof(IDXGIResource1), reinterpret_cast<void **>(&r1.p)));
    CHECK(r1->CreateSharedHandle(nullptr, access_rw, nullptr, &collision) == E_INVALIDARG && !collision);
    r1.reset();
    texture.reset();
    td.MiscFlags = desc().MiscFlags;
    td.ArraySize = 2;
    HR(d.device->CreateTexture2D(&td, nullptr, &texture.p));
    HR(texture->QueryInterface(__uuidof(IDXGIResource1), reinterpret_cast<void **>(&r1.p)));
    CHECK(r1->CreateSharedHandle(nullptr, access_rw, nullptr, &collision) == E_NOTIMPL && !collision);
}
static void window_loss()
{
    struct Window
    {
        HWND value = CreateWindowExW(0, L"STATIC", L"Private shared texture window loss",
                                     WS_POPUP | WS_VISIBLE, 20, 20, 64, 64, nullptr, nullptr, nullptr, nullptr);
        ~Window() { if (value) DestroyWindow(value); }
    } window;
    CHECK(window.value);
    if (!window.value) return;
    Device producer, consumer;
    Ref<IDXGISwapChain> swapchain;
    DXGI_SWAP_CHAIN_DESC sd = {};
    sd.BufferDesc.Width = sd.BufferDesc.Height = 64;
    sd.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    sd.SampleDesc.Count = 1;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.BufferCount = 1;
    sd.OutputWindow = window.value;
    sd.Windowed = TRUE;
    HR(D3D11CreateDeviceAndSwapChain(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                                    nullptr, 0, D3D11_SDK_VERSION, &sd, &swapchain.p, &producer.device.p,
                                    nullptr, &producer.context.p));
    HR(producer.device->QueryInterface(__uuidof(ID3D11Device1), reinterpret_cast<void **>(&producer.v1.p)));
    if (!consumer.init()) return;
    Ref<ID3D11Texture2D> texture, imported;
    Ref<ID3D11RenderTargetView> rtv;
    Ref<IDXGIResource1> resource;
    auto td = desc();
    HR(producer.device->CreateTexture2D(&td, nullptr, &texture.p));
    HR(producer.device->CreateRenderTargetView(texture.p, nullptr, &rtv.p));
    const float blue[] = {0, 0, 1, 1}, red[] = {1, 0, 0, 1};
    producer.context->ClearRenderTargetView(rtv.p, blue);
    CHECK(producer.wait());
    HR(texture->QueryInterface(__uuidof(IDXGIResource1), reinterpret_cast<void **>(&resource.p)));
    Handle shared;
    HR(resource->CreateSharedHandle(nullptr, access_rw, nullptr, &shared.h));
    open(consumer, shared.h, imported);
    if (!imported.p) return;
    Pixels expected;
    expected.fill(BLUE);
    consumer.verify(imported.p, expected, "before original swapchain window destruction");
    CHECK(DestroyWindow(window.value));
    window.value = nullptr;
    producer.context->ClearRenderTargetView(rtv.p, red);
    CHECK(producer.wait());
    expected.fill(RED);
    consumer.verify(imported.p, expected, "backup context publishes after original HWND destruction");
    producer.verify(texture.p, expected, "producer survives original HWND destruction");
}
int main(int argc, char **argv)
{
    setbuf(stdout, nullptr);
    SetEnvironmentVariableA("GHUB_D3D11_SHARED_TEXTURES", "1");
    if (argc == 2 && !lstrcmpA(argv[1], "--window-loss"))
        window_loss();
    else if (argc == 3 || argc == 5)
        child(reinterpret_cast<HANDLE>(static_cast<ULONG_PTR>(strtoull(argv[2], nullptr, 10))),
              !lstrcmpA(argv[1], "--race"),
              argc == 5 ? reinterpret_cast<HANDLE>(static_cast<ULONG_PTR>(strtoull(argv[3], nullptr, 10))) : nullptr,
              argc == 5 ? reinterpret_cast<HANDLE>(static_cast<ULONG_PTR>(strtoull(argv[4], nullptr, 10))) : nullptr);
    else
    {
        run();
        errors_and_names();
    }
    std::printf("Shared textures: %u checks, %u failures\n", checks, failures);
    return failures ? 1 : 0;
}
