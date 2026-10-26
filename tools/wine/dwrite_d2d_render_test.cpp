/* Headless native-D2D integration: render text into a DXGI texture, read it back
 * through D3D11 and check real coverage. No windows or desktop input. */
#include <windows.h>
#include <d3d11.h>
#include <d2d1_1.h>
#include <dwrite.h>
#include <cstdio>
#include <cstring>
#include <initializer_list>
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
static void render()
{
    Ref<ID3D11Device> d3d;
    Ref<ID3D11DeviceContext> immediate;
    Ref<IDXGIDevice> dxgi;
    Ref<ID2D1Factory1> factory;
    Ref<ID2D1Device> device;
    Ref<ID2D1DeviceContext> context;
    Ref<ID3D11Texture2D> texture, staging;
    Ref<IDXGISurface> surface;
    Ref<ID2D1Bitmap1> bitmap;
    Ref<IDWriteFactory> dwrite;
    Ref<IDWriteTextFormat> format;
    Ref<ID2D1SolidColorBrush> brush;
    HR(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0,
                         D3D11_SDK_VERSION, &d3d.p, nullptr, &immediate.p));
    HR(d3d->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void **>(&dxgi.p)));
    HR(D2D1CreateFactory(D2D1_FACTORY_TYPE_MULTI_THREADED, __uuidof(ID2D1Factory1), nullptr,
                         reinterpret_cast<void **>(&factory.p)));
    HR(factory->CreateDevice(dxgi.p, &device.p));
    HR(device->CreateDeviceContext(D2D1_DEVICE_CONTEXT_OPTIONS_NONE, &context.p));
    D3D11_TEXTURE2D_DESC desc = {};
    desc.Width = 128;
    desc.Height = 64;
    desc.MipLevels = 1;
    desc.ArraySize = 1;
    desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    desc.SampleDesc.Count = 1;
    desc.BindFlags = D3D11_BIND_RENDER_TARGET | D3D11_BIND_SHADER_RESOURCE;
    HR(d3d->CreateTexture2D(&desc, nullptr, &texture.p));
    HR(texture->QueryInterface(__uuidof(IDXGISurface), reinterpret_cast<void **>(&surface.p)));
    D2D1_BITMAP_PROPERTIES1 props = {};
    props.pixelFormat = {desc.Format, D2D1_ALPHA_MODE_IGNORE};
    props.dpiX = props.dpiY = 96;
    props.bitmapOptions = D2D1_BITMAP_OPTIONS_TARGET | D2D1_BITMAP_OPTIONS_CANNOT_DRAW;
    HR(context->CreateBitmapFromDxgiSurface(surface.p, &props, &bitmap.p));
    context->SetTarget(bitmap.p);
    desc.BindFlags = 0;
    desc.Usage = D3D11_USAGE_STAGING;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    HR(d3d->CreateTexture2D(&desc, nullptr, &staging.p));
    HR(DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED, __uuidof(IDWriteFactory),
                           reinterpret_cast<IUnknown **>(&dwrite.p)));
    HR(dwrite->CreateTextFormat(L"Arial", nullptr, DWRITE_FONT_WEIGHT_NORMAL, DWRITE_FONT_STYLE_NORMAL,
                                DWRITE_FONT_STRETCH_NORMAL, 24, L"en-US", &format.p));
    HR(context->CreateSolidColorBrush(D2D1::ColorF(D2D1::ColorF::White), &brush.p));
    for (auto aa :
         {D2D1_TEXT_ANTIALIAS_MODE_ALIASED, D2D1_TEXT_ANTIALIAS_MODE_GRAYSCALE, D2D1_TEXT_ANTIALIAS_MODE_CLEARTYPE})
    {
        for (unsigned draw = 0; draw < 3; ++draw)
        {
            context->SetTextAntialiasMode(aa);
            context->BeginDraw();
            context->Clear(D2D1::ColorF(D2D1::ColorF::Black));
            if (draw == 1)
                context->DrawText(L"Ag", 2, format.p, D2D1::RectF(8, 4, 120, 60), brush.p);
            HR(context->EndDraw());
            immediate->CopyResource(staging.p, texture.p);
            D3D11_MAPPED_SUBRESOURCE map = {};
            HR(immediate->Map(staging.p, 0, D3D11_MAP_READ, 0, &map));
            unsigned lit = 0, gray = 0, outside = 0;
            for (unsigned y = 0; y < 64; ++y)
                for (unsigned x = 0; x < 128; ++x)
                {
                    auto p = static_cast<const unsigned char *>(map.pData) + y * map.RowPitch + x * 4;
                    if (p[0] || p[1] || p[2])
                    {
                        ++lit;
                        if (x < 8 || x >= 64 || y < 4 || y >= 60)
                            ++outside;
                    }
                    if (p[0] > 0 && p[0] < 255)
                        ++gray;
                }
            immediate->Unmap(staging.p, 0);
            CHECK(outside == 0);
            if (draw == 1)
            {
                CHECK(lit > 100 && lit < 1200);
                if (aa != D2D1_TEXT_ANTIALIAS_MODE_ALIASED)
                    CHECK(gray > 20);
            }
            else
                CHECK(lit == 0);
            std::printf("Native D2D aa=%u draw=%u lit=%u intermediate=%u outside=%u\n", aa, draw, lit, gray, outside);
        }
    }
    context->SetTarget(nullptr);
}
int main()
{
    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    CHECK(SUCCEEDED(hr));
    if (SUCCEEDED(hr))
    {
        render();
        CoUninitialize();
    }
    std::printf("D2D text pixels: %u checks, %u failures\n", checks, failures);
    return failures ? 1 : 0;
}
