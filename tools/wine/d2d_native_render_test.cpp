/* Integration test for the user's native D2D DLL against the private Wine
 * graphics build. Run in an isolated directory/prefix/display. No WinUI shim. */
#include <windows.h>
#include <d3d11.h>
#include <d2d1_1.h>
#include <cstdio>
#include <cstdlib>

static unsigned failures;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL line %u: %s\n", __LINE__, #x); ++failures; } } while (0)
#define RELEASE(x) do { if (x) (x)->Release(); } while (0)

int main()
{
    ID3D11Device *device = nullptr;
    ID3D11DeviceContext *context = nullptr;
    IDXGIDevice *dxgi = nullptr;
    ID2D1Factory1 *factory = nullptr;
    ID2D1Device *d2d = nullptr;
    ID2D1DeviceContext *draw = nullptr;
    ID3D11Texture2D *source = nullptr, *target = nullptr, *staging = nullptr;
    IDXGISurface *source_surface = nullptr, *target_surface = nullptr;
    ID2D1Bitmap1 *source_bitmap = nullptr, *target_bitmap = nullptr;
    D3D11_TEXTURE2D_DESC td = {};
    static const BYTE pixels[] = {0,0,255,255, 0,255,0,255, 255,0,0,255, 128,64,32,255};
    D3D11_SUBRESOURCE_DATA data = {pixels, 8, 16};
    D2D1_FACTORY_OPTIONS options = {};
    D2D1_BITMAP_PROPERTIES1 props = {};
    D2D1_RECT_F rect = {0,0,2,2};
    D3D11_MAPPED_SUBRESOURCE mapped = {};
    HRESULT hr;
    std::setbuf(stderr, nullptr);
    SetEnvironmentVariableA("GHUB_D3D11_PARTNER_PROBE", "1");
    hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
        D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION, &device, nullptr, &context);
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    hr = device->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void **>(&dxgi));
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    hr = D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, __uuidof(ID2D1Factory1),
        &options, reinterpret_cast<void **>(&factory));
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    hr = factory->CreateDevice(dxgi, &d2d);
    std::fprintf(stderr, "D2D CreateDevice: %#lx\n", static_cast<unsigned long>(hr));
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    hr = d2d->CreateDeviceContext(D2D1_DEVICE_CONTEXT_OPTIONS_NONE, &draw);
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    td.Width = td.Height = 2; td.MipLevels = td.ArraySize = td.SampleDesc.Count = 1;
    td.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    td.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    hr = device->CreateTexture2D(&td, &data, &source);
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    td.BindFlags |= D3D11_BIND_RENDER_TARGET;
    hr = device->CreateTexture2D(&td, nullptr, &target);
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    hr = source->QueryInterface(__uuidof(IDXGISurface), reinterpret_cast<void **>(&source_surface));
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    hr = target->QueryInterface(__uuidof(IDXGISurface), reinterpret_cast<void **>(&target_surface));
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    props.pixelFormat.format = td.Format;
    props.pixelFormat.alphaMode = D2D1_ALPHA_MODE_PREMULTIPLIED;
    props.dpiX = props.dpiY = 96;
    hr = draw->CreateBitmapFromDxgiSurface(source_surface, &props, &source_bitmap);
    std::fprintf(stderr, "D2D source bitmap: %#lx\n", static_cast<unsigned long>(hr));
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    props.bitmapOptions = D2D1_BITMAP_OPTIONS_TARGET;
    hr = draw->CreateBitmapFromDxgiSurface(target_surface, &props, &target_bitmap);
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    draw->SetTarget(target_bitmap);
    draw->BeginDraw();
    draw->DrawBitmap(source_bitmap, &rect, 1.0f, D2D1_INTERPOLATION_MODE_NEAREST_NEIGHBOR, &rect, nullptr);
    hr = draw->EndDraw();
    std::fprintf(stderr, "D2D EndDraw: %#lx\n", static_cast<unsigned long>(hr));
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    draw->SetTarget(nullptr);
    td.BindFlags = 0; td.Usage = D3D11_USAGE_STAGING; td.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    hr = device->CreateTexture2D(&td, nullptr, &staging);
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    context->CopyResource(staging, target);
    hr = context->Map(staging, 0, D3D11_MAP_READ, 0, &mapped);
    CHECK(SUCCEEDED(hr)); if (FAILED(hr)) goto done;
    for (unsigned i=0; i<16; ++i) {
        const BYTE *row = static_cast<const BYTE *>(mapped.pData) + (i/8)*mapped.RowPitch;
        CHECK(std::abs(static_cast<int>(row[i%8]) - pixels[i]) <= 1);
    }
    context->Unmap(staging, 0);
done:
    if (draw) draw->SetTarget(nullptr);
    RELEASE(target_bitmap); RELEASE(source_bitmap); RELEASE(target_surface); RELEASE(source_surface);
    RELEASE(staging); RELEASE(target); RELEASE(source); RELEASE(draw); RELEASE(d2d); RELEASE(factory);
    RELEASE(dxgi); RELEASE(context); RELEASE(device);
    std::fprintf(stderr, "Native D2D bitmap draw and readback: %s (%u failures)\n", failures ? "FAIL" : "PASS", failures);
    return !!failures;
}
