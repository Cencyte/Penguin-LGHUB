// Caller-visible texture formats and actual presentation, including the native
// Direct2D surface-factory path used by the tray menu. Run on private Xvfb only.
#include <initializer_list>
static void formats_run(HMODULE module, bool native_d2d)
{
    SharedApi api(module);
    if (!api.device || !api.shared || !api.target) return;
    Ref<ID3D11Device> d3d;
    Ref<ID3D11DeviceContext> context;
    Ref<IDXGIDevice> dxgi;
    Ref<IDCompositionDesktopDevice> device;
    Ref<IDCompositionVisual2> root;
    Ref<IDCompositionTarget> target;
    HR(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                         nullptr, 0, D3D11_SDK_VERSION, &d3d.p, nullptr, &context.p));
    HR(d3d->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void **>(&dxgi.p)));
    HR(api.device(dxgi.p, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&device.p)));
    HR(device->CreateVisual(&root.p));
    HR(root->SetOffsetX(16.0f));
    HR(root->SetOffsetY(16.0f));
    Window window;
    CHECK(window.handle);
    if (!window.handle) return;
    pump();
    SetEnvironmentVariableW(L"GHUB_DCOMP_POPUP_TARGET", L"1");
    HR(device->CreateTargetForHwnd(window.handle, TRUE, &target.p));
    HR(target->SetRoot(root.p));
    RECT partial{1, 1, 2, 2};
    POINT offset;
    for (auto format : {DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_FORMAT_R8G8B8A8_UNORM})
    for (auto alpha : {DXGI_ALPHA_MODE_PREMULTIPLIED, DXGI_ALPHA_MODE_IGNORE, DXGI_ALPHA_MODE_UNSPECIFIED})
    {
        Ref<IDCompositionSurface> surface;
        Ref<IDXGISurface> update;
        Ref<ID3D11Texture2D> texture;
        Ref<ID3D11RenderTargetView> view;
        HR(device->CreateSurface(3, 3, format, alpha, &surface.p));
        HR(root->SetContent(surface.p));
        HR(device->Commit());
        pixel(window.handle, 1, 1, 255, 255, 255); // uninitialized means transparent even for IGNORE
        CHECK(surface->BeginDraw(&partial, __uuidof(IDXGISurface), reinterpret_cast<void **>(&update.p), &offset)
              == E_INVALIDARG);
        CHECK(!update.p);
        Ref<IUnknown> invalid_draw;
        CHECK(surface->BeginDraw(nullptr, __uuidof(ID3D11Device), reinterpret_cast<void **>(&invalid_draw.p),
                                 &offset) == E_NOINTERFACE && !invalid_draw.p);
        if (alpha == DXGI_ALPHA_MODE_IGNORE)
        {
            HR(surface->BeginDraw(nullptr, __uuidof(IDXGISurface), reinterpret_cast<void **>(&update.p), &offset));
            HR(update->QueryInterface(__uuidof(ID3D11Texture2D), reinterpret_cast<void **>(&texture.p)));
        }
        else
        {
            HR(surface->BeginDraw(nullptr, __uuidof(ID3D11Texture2D), reinterpret_cast<void **>(&texture.p), &offset));
            HR(texture->QueryInterface(__uuidof(IDXGISurface), reinterpret_cast<void **>(&update.p)));
        }
        CHECK(offset.x == 0 && offset.y == 0);
        DXGI_SURFACE_DESC description = {};
        HR(update->GetDesc(&description));
        CHECK(description.Format == format && description.Width == 3 && description.Height == 3);
        D3D11_TEXTURE2D_DESC texture_description = {};
        texture->GetDesc(&texture_description);
        CHECK(texture_description.Format == format && texture_description.Width == 3 && texture_description.Height == 3);
        HR(d3d->CreateRenderTargetView(texture.p, nullptr, &view.p));
        const bool premult = alpha == DXGI_ALPHA_MODE_PREMULTIPLIED;
        const float color[]{premult ? .5f : 1.f, 0, 0, premult ? .5f : 0.f};
        context->ClearRenderTargetView(view.p, color);
        HR(surface->EndDraw());
        HR(device->Commit());
        pixel(window.handle, 1, 1, 255, premult ? 127 : 0, premult ? 127 : 0);
        HR(fill(surface.p, d3d.p, context.p, &partial, 0, 0, premult ? .5f : 1.f, premult ? .5f : 0.f));
        pixel(window.handle, 1, 1, 255, premult ? 127 : 0, premult ? 127 : 0); // no early publication
        HR(device->Commit());
        pixel(window.handle, 1, 1, premult ? 127 : 0, premult ? 127 : 0, 255);
        pixel(window.handle, 2, 2, 255, premult ? 127 : 0, premult ? 127 : 0); // row pitch and untouched rows
        HR(root->SetContent(nullptr));
    }
    Ref<IDCompositionSurface> invalid;
    CHECK(FAILED(device->CreateSurface(3, 3, DXGI_FORMAT_UNKNOWN, DXGI_ALPHA_MODE_IGNORE, &invalid.p)) && !invalid.p);
    CHECK(device->CreateSurface(3, 3, DXGI_FORMAT_R8G8B8A8_UNORM, DXGI_ALPHA_MODE_STRAIGHT, &invalid.p)
          == E_INVALIDARG && !invalid.p);
    if (native_d2d)
    {
        Ref<ID2D1Factory1> factory;
        Ref<ID2D1Device> d2d;
        Ref<IDCompositionSurfaceFactory> surfaces;
        Ref<IDCompositionSurface> surface;
        Ref<ID2D1DeviceContext> draw;
        D2D1_FACTORY_OPTIONS options = {};
        HR(D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, __uuidof(ID2D1Factory1), &options,
                             reinterpret_cast<void **>(&factory.p)));
        HR(factory->CreateDevice(dxgi.p, &d2d.p));
        HR(device->CreateSurfaceFactory(d2d.p, &surfaces.p));
        HR(surfaces->CreateSurface(3, 3, DXGI_FORMAT_R8G8B8A8_UNORM, DXGI_ALPHA_MODE_PREMULTIPLIED, &surface.p));
        surfaces.reset(); // surface must retain its renderer independently
        d2d.reset();
        factory.reset();
        HR(surface->BeginDraw(nullptr, __uuidof(ID2D1DeviceContext), reinterpret_cast<void **>(&draw.p), &offset));
        CHECK(draw->GetPixelFormat().format == DXGI_FORMAT_R8G8B8A8_UNORM);
        CHECK(draw->GetPixelFormat().alphaMode == D2D1_ALPHA_MODE_PREMULTIPLIED);
        const D2D1_COLOR_F red{1, 0, 0, .5f}, blue{0, 0, 1, .5f};
        draw->Clear(&red);
        HR(surface->EndDraw());
        draw.reset();
        HR(root->SetContent(surface.p));
        HR(device->Commit());
        pixel(window.handle, 1, 1, 255, 127, 127);
        HR(surface->BeginDraw(&partial, __uuidof(ID2D1DeviceContext), reinterpret_cast<void **>(&draw.p), &offset));
        draw->Clear(&blue);
        HR(surface->EndDraw());
        draw.reset();
        HR(device->Commit());
        pixel(window.handle, 1, 1, 127, 127, 255);
        pixel(window.handle, 2, 2, 255, 127, 127);
        // Shared publication must not swizzle again or lose premultiplied alpha.
        HR(target->SetRoot(nullptr));
        HR(device->Commit());
        target.reset();
        Handle handle;
        Ref<IDCompositionTarget> publisher, destination;
        Ref<IDCompositionDesktopDevice> consumer;
        Ref<IDCompositionVisual> imported;
        HR(api.shared(&handle.value));
        HR(api.target(handle.value, &publisher.p));
        HR(publisher->SetRoot(root.p));
        HR(api.device(nullptr, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&consumer.p)));
        HR(api.open(consumer.p, handle.value, shared_visual_iid, reinterpret_cast<void **>(&imported.p)));
        HR(consumer->CreateTargetForHwnd(window.handle, TRUE, &destination.p));
        HR(destination->SetRoot(imported.p));
        HR(device->Commit());
        HR(consumer->Commit());
        pixel(window.handle, 1, 1, 127, 127, 255);
        pixel(window.handle, 2, 2, 255, 127, 127);
        HR(destination->SetRoot(nullptr));
        HR(consumer->Commit());
        HR(publisher->SetRoot(nullptr));
        HR(device->Commit());
    }
    else
    {
        HR(target->SetRoot(nullptr));
        HR(device->Commit());
    }
    std::fputs("BGRA/RGBA texture, alpha, partial update and shared publication checks complete\n", stderr);
}
