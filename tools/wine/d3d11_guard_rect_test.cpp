/* Private D3D11 guard-rectangle ABI and real pixel preservation tests.
 * Run with the lab D3D11/WineD3D pair on private Xvfb. The native-D2D
 * integration also needs patch0022 and the user's d2d1.dll, never distributed. */
#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <d3dcompiler.h>
#include <d2d1_1.h>
#include <cstdio>
#include <cstring>
#include <array>
#include <initializer_list>
struct Partner : IUnknown
{
    virtual HRESULT STDMETHODCALLTYPE Begin() = 0;
    virtual void STDMETHODCALLTYPE End() = 0;
    virtual HRESULT STDMETHODCALLTYPE Texture(const D3D11_TEXTURE2D_DESC *, const D3D11_SUBRESOURCE_DATA *,
                                              ID3D11Texture2D **) = 0;
    virtual void STDMETHODCALLTYPE Guard(ID3D11Texture2D *, RECT) = 0;
    virtual void STDMETHODCALLTYPE Empty(ID3D11Texture2D *) = 0;
    virtual void STDMETHODCALLTYPE Unguarded(ID3D11Texture2D *) = 0;
    virtual void STDMETHODCALLTYPE Slot9() = 0;
    virtual void STDMETHODCALLTYPE Slot10() = 0;
    virtual UINT STDMETHODCALLTYPE Caps() = 0;
    virtual void STDMETHODCALLTYPE Slot12() = 0;
    virtual void STDMETHODCALLTYPE Slot13() = 0;
    virtual void STDMETHODCALLTYPE Get(ID3D11Texture2D *, BOOL *, RECT *) = 0;
};
static const GUID partner_iid = {0x26c5dc23, 0xe49c, 0x4b0a, {0x8f, 0x79, 0xe7, 0xb1, 0xac, 0x80, 0x4d, 0x32}};
template <class T> struct Ref
{
    T *p = nullptr;
    Ref() = default;
    Ref(const Ref &) = delete;
    Ref &operator=(const Ref &) = delete;
    ~Ref()
    {
        if (p)
            p->Release();
    }
    T *operator->() const
    {
        return p;
    }
};
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
        HRESULT h = (x);                                                                                               \
        CHECK(SUCCEEDED(h));                                                                                           \
        if (FAILED(h))                                                                                                 \
        {                                                                                                              \
            std::printf("HRESULT %08lx: %s\n", h, #x);                                                                 \
            return;                                                                                                    \
        }                                                                                                              \
    } while (0)
constexpr UINT W = 12, H = 10;
using Pixels = std::array<UINT, W * H>;
static const RECT all = {0, 0, W, H}, roi = {2, 3, 9, 8};
static const float black[4] = {0, 0, 0, 1}, red[4] = {1, 0, 0, 1}, green[4] = {0, 1, 0, 1};
static constexpr UINT BLACK = 0xff000000, RED = 0xff0000ff, GREEN = 0xff00ff00;
static bool contains(RECT r, UINT x, UINT y)
{
    return (LONG)x >= r.left && (LONG)x < r.right && (LONG)y >= r.top && (LONG)y < r.bottom;
}
static void fill(Pixels &data, RECT rect, UINT value)
{
    for (UINT y = 0; y < H; ++y)
        for (UINT x = 0; x < W; ++x)
            if (contains(rect, x, y))
                data[y * W + x] = value;
}
static void verify(ID3D11DeviceContext *c, ID3D11Texture2D *source, ID3D11Texture2D *readback, const Pixels &expected,
                   const char *label)
{
    c->CopyResource(readback, source);
    D3D11_MAPPED_SUBRESOURCE map = {};
    HR(c->Map(readback, 0, D3D11_MAP_READ, 0, &map));
    unsigned mismatch = 0;
    for (UINT y = 0; y < H; ++y)
        for (UINT x = 0; x < W; ++x)
        {
            UINT actual;
            std::memcpy(&actual, (BYTE *)map.pData + y * map.RowPitch + x * 4, 4);
            if (actual != expected[y * W + x])
                ++mismatch;
        }
    c->Unmap(readback, 0);
    std::printf("pixels %s: %u mismatches\n", label, mismatch);
    CHECK(mismatch == 0);
}
static void run()
{
    SetEnvironmentVariableA("GHUB_D3D11_PARTNER_PROBE", "1");
    const bool shared = GetEnvironmentVariableA("GHUB_GUARD_TEST_SHARED", nullptr, 0) != 0;
    if (shared)
        SetEnvironmentVariableA("GHUB_D3D11_SHARED_TEXTURES", "1");
    Ref<ID3D11Device> device;
    Ref<ID3D11DeviceContext> context;
    Ref<Partner> partner;
    HR(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0,
                         D3D11_SDK_VERSION, &device.p, nullptr, &context.p));
    HR(device->QueryInterface(partner_iid, reinterpret_cast<void **>(&partner.p)));
    {
        Ref<IUnknown> a, b;
        HR(device->QueryInterface(IID_IUnknown, reinterpret_cast<void **>(&a.p)));
        HR(partner->QueryInterface(IID_IUnknown, reinterpret_cast<void **>(&b.p)));
        CHECK(a.p == b.p);
    }
    D3D11_TEXTURE2D_DESC td = {};
    td.Width = W;
    td.Height = H;
    td.MipLevels = td.ArraySize = td.SampleDesc.Count = 1;
    td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    td.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    if (shared)
        td.MiscFlags = D3D11_RESOURCE_MISC_SHARED | D3D11_RESOURCE_MISC_SHARED_NTHANDLE;
    HR(partner->Begin());
    HR(partner->Begin());
    partner->End();
    Ref<ID3D11Texture2D> texture, other, staging;
    Ref<ID3D11RenderTargetView> rt, other_rt;
    HR(partner->Texture(&td, nullptr, &texture.p));
    HR(device->CreateTexture2D(&td, nullptr, &other.p));
    if (shared)
    {
        for (auto *target : {texture.p, other.p})
        {
            Ref<IDXGIResource1> resource;
            HR(target->QueryInterface(__uuidof(IDXGIResource1), reinterpret_cast<void **>(&resource.p)));
            HANDLE handle = nullptr;
            HR(resource->CreateSharedHandle(nullptr, DXGI_SHARED_RESOURCE_READ | DXGI_SHARED_RESOURCE_WRITE,
                                            nullptr, &handle));
            CHECK(CloseHandle(handle));
        }
    }
    HR(device->CreateRenderTargetView(texture.p, nullptr, &rt.p));
    HR(device->CreateRenderTargetView(other.p, nullptr, &other_rt.p));
    D3D11_TEXTURE2D_DESC got = {};
    texture->GetDesc(&got);
    CHECK(got.Width == W && got.Height == H && got.Format == td.Format);
    td.BindFlags = 0;
    td.MiscFlags = 0;
    td.Usage = D3D11_USAGE_STAGING;
    td.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    HR(device->CreateTexture2D(&td, nullptr, &staging.p));
    Pixels expected;
    expected.fill(BLACK);
    auto reset = [&]() {
        partner->Unguarded(texture.p);
        context->ClearRenderTargetView(rt.p, black);
        expected.fill(BLACK);
    };
    auto check = [&](const char *label) {
        partner->Unguarded(texture.p);
        verify(context.p, texture.p, staging.p, expected, label);
    };
    reset();
    partner->Guard(texture.p, roi);
    BOOL guarded = FALSE;
    RECT rect = {};
    partner->Get(texture.p, &guarded, &rect);
    CHECK(guarded && EqualRect(&rect, &roi));
    texture->GetDesc(&got);
    CHECK((got.MiscFlags & D3D11_RESOURCE_MISC_GUARDED) && !(got.BindFlags & D3D11_BIND_SHADER_RESOURCE));
    context->ClearRenderTargetView(rt.p, red);
    fill(expected, roi, RED);
    check("guarded clear; unguard before GPU completion");
    reset();
    partner->Empty(texture.p);
    partner->Get(texture.p, &guarded, &rect);
    CHECK(!guarded && IsRectEmpty(&rect));
    context->ClearRenderTargetView(rt.p, red);
    check("empty suppresses clear");
    partner->Get(texture.p, &guarded, &rect);
    CHECK(!guarded);
    context->ClearRenderTargetView(rt.p, green);
    expected.fill(GREEN);
    check("unguarded clear");
    reset();
    RECT outside = {-3, -2, 4, 5};
    partner->Guard(texture.p, outside);
    context->ClearRenderTargetView(rt.p, red);
    fill(expected, outside, RED);
    check("rectangle clipped to texture");
    Pixels gradient;
    for (UINT y = 0; y < H; ++y)
        for (UINT x = 0; x < W; ++x)
            gradient[y * W + x] = BLACK | (x + 1) | ((y + 1) << 8);
    reset();
    partner->Guard(texture.p, roi);
    context->UpdateSubresource(texture.p, 0, nullptr, gradient.data(), W * 4, W * H * 4);
    for (UINT y = 0; y < H; ++y)
        for (UINT x = 0; x < W; ++x)
            if (contains(roi, x, y))
                expected[y * W + x] = gradient[y * W + x];
    check("guarded upload source offsets");
    reset();
    D3D11_BOX box = {1, 2, 0, 11, 9, 1};
    partner->Guard(texture.p, roi);
    context->UpdateSubresource(texture.p, 0, &box, gradient.data(), W * 4, W * H * 4);
    for (UINT y = 0; y < H; ++y)
        for (UINT x = 0; x < W; ++x)
            if (contains(roi, x, y))
                expected[y * W + x] = gradient[(y - 2) * W + x - 1];
    check("boxed upload offsets");
    reset();
    context->UpdateSubresource(texture.p, 0, nullptr, gradient.data(), W * 4, W * H * 4);
    context->ClearRenderTargetView(other_rt.p, black);
    partner->Guard(texture.p, roi);
    context->CopyResource(other.p, texture.p);
    expected.fill(BLACK);
    for (UINT y = 0; y < H; ++y)
        for (UINT x = 0; x < W; ++x)
            if (contains(roi, x, y))
                expected[y * W + x] = gradient[y * W + x];
    verify(context.p, other.p, staging.p, expected, "source copy guard");
    partner->Unguarded(texture.p);
    context->ClearRenderTargetView(other_rt.p, black);
    partner->Guard(other.p, roi);
    context->CopyResource(other.p, texture.p);
    partner->Unguarded(other.p);
    verify(context.p, other.p, staging.p, expected, "destination copy guard");
    context->ClearRenderTargetView(other_rt.p, black);
    RECT dst_roi = {5, 4, 11, 9};
    partner->Guard(texture.p, roi);
    partner->Guard(other.p, dst_roi);
    D3D11_BOX from = {1, 1, 0, 9, 8, 1};
    context->CopySubresourceRegion(other.p, 0, 3, 2, 0, texture.p, 0, &from);
    partner->Unguarded(other.p);
    expected.fill(BLACK);
    for (UINT y = 2; y < 9; ++y)
        for (UINT x = 3; x < 11; ++x)
            if (contains(dst_roi, x, y) && contains(roi, x - 2, y - 1))
                expected[y * W + x] = gradient[(y - 1) * W + x - 2];
    verify(context.p, other.p, staging.p, expected, "both copy guards with translated boxes");
    // Shader draw, indexed and indirect paths must intersect, not replace, scissor.
    Ref<ID3DBlob> vs_code, ps_code, errors;
    Ref<ID3D11VertexShader> vs;
    Ref<ID3D11PixelShader> ps;
    const char *vsrc = "float4 main(uint id:SV_VertexID):SV_Position {float2 uv=float2((id<<1)&2,id&2);return "
                       "float4(uv*float2(2,-2)+float2(-1,1),0,1);}";
    const char *psrc = "float4 main():SV_Target {return float4(1,0,0,1);}";
    HR(D3DCompile(vsrc, std::strlen(vsrc), nullptr, nullptr, nullptr, "main", "vs_4_0", 0, 0, &vs_code.p, &errors.p));
    HR(D3DCompile(psrc, std::strlen(psrc), nullptr, nullptr, nullptr, "main", "ps_4_0", 0, 0, &ps_code.p, nullptr));
    HR(device->CreateVertexShader(vs_code->GetBufferPointer(), vs_code->GetBufferSize(), nullptr, &vs.p));
    HR(device->CreatePixelShader(ps_code->GetBufferPointer(), ps_code->GetBufferSize(), nullptr, &ps.p));
    D3D11_VIEWPORT vp = {0, 0, W, H, 0, 1};
    context->RSSetViewports(1, &vp);
    context->VSSetShader(vs.p, nullptr, 0);
    context->PSSetShader(ps.p, nullptr, 0);
    context->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    context->OMSetRenderTargets(1, &rt.p, nullptr);
    Ref<ID3D11RasterizerState> rs;
    D3D11_RASTERIZER_DESC rd = {};
    rd.FillMode = D3D11_FILL_SOLID;
    rd.CullMode = D3D11_CULL_NONE;
    rd.DepthClipEnable = TRUE;
    rd.ScissorEnable = TRUE;
    HR(device->CreateRasterizerState(&rd, &rs.p));
    RECT scissor = {6, 0, 11, 10}, intersection = {6, 3, 9, 8};
    context->RSSetScissorRects(1, &scissor);
    reset();
    partner->Guard(texture.p, roi);
    context->RSSetState(nullptr);
    context->Draw(3, 0);
    fill(expected, roi, RED);
    check("draw enforces guard with scissor disabled");
    {
        Ref<ID3D11RasterizerState> current;
        context->RSGetState(&current.p);
        CHECK(current.p == nullptr);
        UINT n = 1;
        RECT got_scissor = {};
        context->RSGetScissorRects(&n, &got_scissor);
        CHECK(n == 1 && EqualRect(&got_scissor, &scissor));
    }
    reset();
    partner->Guard(texture.p, roi);
    context->RSSetState(rs.p);
    context->DrawInstanced(3, 1, 0, 0);
    fill(expected, intersection, RED);
    check("instanced draw intersects scissor");
    {
        Ref<ID3D11RasterizerState> current;
        context->RSGetState(&current.p);
        CHECK(current.p == rs.p);
    }
    Ref<ID3D11Buffer> indices, args;
    UINT ix[3] = {0, 1, 2};
    D3D11_BUFFER_DESC bd = {};
    bd.ByteWidth = sizeof(ix);
    bd.BindFlags = D3D11_BIND_INDEX_BUFFER;
    D3D11_SUBRESOURCE_DATA sd = {ix, 0, 0};
    HR(device->CreateBuffer(&bd, &sd, &indices.p));
    context->IASetIndexBuffer(indices.p, DXGI_FORMAT_R32_UINT, 0);
    reset();
    partner->Guard(texture.p, roi);
    context->DrawIndexed(3, 0, 0);
    fill(expected, intersection, RED);
    check("indexed draw guard");
    UINT arguments[4] = {3, 1, 0, 0};
    bd.ByteWidth = sizeof(arguments);
    bd.BindFlags = 0;
    bd.MiscFlags = D3D11_RESOURCE_MISC_DRAWINDIRECT_ARGS;
    sd.pSysMem = arguments;
    HR(device->CreateBuffer(&bd, &sd, &args.p));
    reset();
    partner->Guard(texture.p, roi);
    context->DrawInstancedIndirect(args.p, 0);
    fill(expected, intersection, RED);
    check("indirect draw guard");
    reset();
    partner->Empty(texture.p);
    context->Draw(3, 0);
    check("empty suppresses shader writes");
    // A deferred draw retains its effective guard and cached rasterizer object.
    {
        Ref<ID3D11DeviceContext> deferred;
        Ref<ID3D11CommandList> commands;
        HR(device->CreateDeferredContext(0, &deferred.p));
        reset();
        partner->Guard(texture.p, roi);
        deferred->RSSetViewports(1, &vp);
        deferred->RSSetState(rs.p);
        deferred->RSSetScissorRects(1, &scissor);
        deferred->VSSetShader(vs.p, nullptr, 0);
        deferred->PSSetShader(ps.p, nullptr, 0);
        deferred->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        deferred->OMSetRenderTargets(1, &rt.p, nullptr);
        deferred->Draw(3, 0);
        HR(deferred->FinishCommandList(FALSE, &commands.p));
        context->ExecuteCommandList(commands.p, TRUE);
        fill(expected, intersection, RED);
        check("deferred command-list guard");
    }
    // MRT guard violations must not write any render target.
    reset();
    context->ClearRenderTargetView(other_rt.p, black);
    partner->Guard(texture.p, roi);
    ID3D11RenderTargetView *both[2] = {rt.p, other_rt.p};
    context->OMSetRenderTargets(2, both, nullptr);
    context->Draw(3, 0);
    check("MRT rejected");
    verify(context.p, other.p, staging.p, expected, "MRT other target preserved");
    context->OMSetRenderTargets(0, nullptr, nullptr);
    context->ClearState();
    // Use native Direct2D on the same real guarded DXGI texture.
    Ref<IDXGIDevice> dxgi;
    Ref<IDXGISurface> surface;
    Ref<ID2D1Factory1> d2df;
    Ref<ID2D1Device> d2dd;
    Ref<ID2D1DeviceContext> d2dc;
    Ref<ID2D1Bitmap1> bitmap;
    Ref<ID2D1SolidColorBrush> brush;
    HR(device->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void **>(&dxgi.p)));
    HR(D2D1CreateFactory(D2D1_FACTORY_TYPE_MULTI_THREADED, __uuidof(ID2D1Factory1), nullptr,
                         reinterpret_cast<void **>(&d2df.p)));
    HR(d2df->CreateDevice(dxgi.p, &d2dd.p));
    HR(d2dd->CreateDeviceContext(D2D1_DEVICE_CONTEXT_OPTIONS_NONE, &d2dc.p));
    HR(texture->QueryInterface(__uuidof(IDXGISurface), reinterpret_cast<void **>(&surface.p)));
    D2D1_BITMAP_PROPERTIES1 bp = {};
    bp.pixelFormat = {DXGI_FORMAT_R8G8B8A8_UNORM, D2D1_ALPHA_MODE_IGNORE};
    bp.dpiX = bp.dpiY = 96;
    bp.bitmapOptions = D2D1_BITMAP_OPTIONS_TARGET | D2D1_BITMAP_OPTIONS_CANNOT_DRAW;
    HR(d2dc->CreateBitmapFromDxgiSurface(surface.p, &bp, &bitmap.p));
    d2dc->SetTarget(bitmap.p);
    HR(d2dc->CreateSolidColorBrush(D2D1::ColorF(D2D1::ColorF::Red), &brush.p));
    d2dc->SetAntialiasMode(D2D1_ANTIALIAS_MODE_ALIASED);
    reset();
    partner->Guard(texture.p, roi);
    d2dc->BeginDraw();
    d2dc->FillRectangle(D2D1::RectF(0, 0, W, H), brush.p);
    HR(d2dc->EndDraw());
    fill(expected, roi, RED);
    check("native D2D guarded geometry");
    d2dc->SetTarget(nullptr);
    context->ClearState();
    partner->End();
    // Live guard resources retain support after the explicit scope ends.
    // Unsupported resource shapes still fail explicitly.
    td.Usage = D3D11_USAGE_DEFAULT;
    td.CPUAccessFlags = 0;
    td.BindFlags = D3D11_BIND_RENDER_TARGET;
    Ref<ID3D11Texture2D> bad;
    HR(partner->Texture(&td, nullptr, &bad.p));
    HR(partner->Begin());
    td.MipLevels = 2;
    bad.p->Release();
    bad.p = nullptr;
    CHECK(partner->Texture(&td, nullptr, &bad.p) == E_NOTIMPL && !bad.p);
    partner->End();
}
static void lifetime()
{
    ID3D11Device *device = nullptr;
    ID3D11DeviceContext *context = nullptr;
    Partner *partner = nullptr;
    HR(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0, D3D11_SDK_VERSION, &device, nullptr,
                         &context));
    HR(device->QueryInterface(partner_iid, reinterpret_cast<void **>(&partner)));
    D3D11_TEXTURE2D_DESC td = {};
    td.Width = td.Height = 4;
    td.MipLevels = td.ArraySize = td.SampleDesc.Count = 1;
    td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    td.BindFlags = D3D11_BIND_RENDER_TARGET;
    ID3D11Texture2D *a = nullptr, *b = nullptr;
    CHECK(partner->Texture(&td, nullptr, &a) == E_INVALIDARG && !a);
    HR(partner->Begin());
    HR(partner->Texture(&td, nullptr, &a));
    partner->Guard(a, RECT{1, 1, 3, 3});
    partner->End();
    HR(partner->Texture(&td, nullptr, &b));
    b->Release();
    b = nullptr;
    a->Release();
    a = nullptr;
    CHECK(partner->Texture(&td, nullptr, &b) == E_INVALIDARG && !b);
    partner->Release();
    context->Release();
    CHECK(device->Release() == 0);
}
int main()
{
    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    CHECK(SUCCEEDED(hr));
    if (SUCCEEDED(hr))
    {
        run();
        lifetime();
        CoUninitialize();
    }
    std::printf("Guard rectangles: %u checks, %u failures\n", checks, failures);
    return failures ? 1 : 0;
}
