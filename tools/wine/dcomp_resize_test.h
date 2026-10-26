// Private logical-surface resize through the bundled FrameworkUdk. Its discard
// semantics differ from the public IDCompositionVirtualSurface::Resize API.
static void resize_run(HMODULE module)
{
    SharedApi api(module);
    using Resize = HRESULT(WINAPI *)(IUnknown *, UINT, UINT);
    auto wrapped = api.framework ? reinterpret_cast<Resize>(reinterpret_cast<void *>(
        GetProcAddress(api.framework, "DCompPrivates_ResizeDCompositionSurface"))) : nullptr;
    CHECK(!api.framework || wrapped);
    if (api.framework && !wrapped) return;
    static const IID partner_iid = {0x5680c979,0x14c8,0x48b5,{0x8b,0x92,0x32,0x47,0x37,0x9f,0xd9,0x07}};
    static const IID logical_iid = {0xbb8a4953,0x2c99,0x4f5a,{0x96,0xf5,0x48,0x19,0x02,0x7f,0xa3,0xac}};
    const HRESULT drawing = static_cast<HRESULT>(0x88980801);
    Ref<ID3D11Device> d3d;
    Ref<ID3D11DeviceContext> context;
    Ref<IDXGIDevice> dxgi;
    Ref<IDCompositionDesktopDevice> device, consumer;
    Ref<IDCompositionVisual2> root;
    Ref<IDCompositionVisual> imported;
    Ref<IDCompositionTarget> publisher, target;
    Handle handle;
    HR(D3D11CreateDevice(nullptr,D3D_DRIVER_TYPE_HARDWARE,nullptr,D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                         nullptr,0,D3D11_SDK_VERSION,&d3d.p,nullptr,&context.p));
    HR(d3d->QueryInterface(__uuidof(IDXGIDevice),reinterpret_cast<void **>(&dxgi.p)));
    HR(api.device(dxgi.p,__uuidof(IDCompositionDesktopDevice),reinterpret_cast<void **>(&device.p)));
    HR(api.device(nullptr,__uuidof(IDCompositionDesktopDevice),reinterpret_cast<void **>(&consumer.p)));
    HR(device->CreateVisual(&root.p)); HR(root->SetOffsetX(16)); HR(root->SetOffsetY(16));
    HR(api.shared(&handle.value));
    HR(api.open(device.p,handle.value,shared_target_iid,reinterpret_cast<void **>(&publisher.p)));
    HR(publisher->SetRoot(root.p));
    HR(api.open(consumer.p,handle.value,shared_visual_iid,reinterpret_cast<void **>(&imported.p)));
    Window window;
    HR(consumer->CreateTargetForHwnd(window.handle,TRUE,&target.p));
    HR(target->SetRoot(imported.p)); HR(consumer->Commit());
    for (auto format : {DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_FORMAT_R8G8B8A8_UNORM})
    for (auto alpha : {DXGI_ALPHA_MODE_PREMULTIPLIED, DXGI_ALPHA_MODE_IGNORE})
    {
        Ref<IDCompositionSurface> surface, public_alias, other;
        Ref<IUnknown> partner, identity, partner_identity, rejected;
        HR(device->CreateSurface(3,3,format,alpha,&surface.p));
        HR(device->CreateSurface(2,2,format,alpha,&other.p));
        HR(surface->QueryInterface(partner_iid,reinterpret_cast<void **>(&partner.p)));
        HR(partner->QueryInterface(logical_iid,reinterpret_cast<void **>(&public_alias.p)));
        CHECK(public_alias.p == surface.p);
        HR(partner->QueryInterface(__uuidof(IUnknown),reinterpret_cast<void **>(&partner_identity.p)));
        HR(surface->QueryInterface(__uuidof(IUnknown),reinterpret_cast<void **>(&identity.p)));
        CHECK(identity.p == partner_identity.p);
        CHECK(partner->QueryInterface(partner_iid,nullptr) == E_POINTER);
        CHECK(partner->QueryInterface(__uuidof(ID3D11Device),reinterpret_cast<void **>(&rejected.p))
              == E_NOINTERFACE && !rejected.p);
        auto direct = reinterpret_cast<Resize>((*reinterpret_cast<void ***>(partner.p))[10]);
        auto resize = [&](UINT w, UINT h) { return wrapped ? wrapped(surface.p,w,h) : direct(partner.p,w,h); };
        HR(root->SetContent(surface.p));
        const bool premult = alpha == DXGI_ALPHA_MODE_PREMULTIPLIED;
        const float value = premult ? .5f : 1.f;
        const float opacity = premult ? .5f : 0.f;
        const int background = premult ? 127 : 0;
        HR(fill(surface.p,d3d.p,context.p,nullptr,value,0,0,opacity)); HR(device->Commit());
        pixel(window.handle,1,1,255,background,background);

        // Failed resize is atomic and leaves an in-flight texture usable.
        HR(fill(surface.p,d3d.p,context.p,nullptr,0,value,0,opacity,false));
        CHECK(resize(5,4) == drawing); CHECK(resize(3,3) == drawing);
        CHECK(resize(0,3) == drawing);
        HR(surface->EndDraw());
        CHECK(resize(0,3) == E_INVALIDARG); CHECK(resize(3,0) == E_INVALIDARG);
        CHECK(resize(16385,1) == E_INVALIDARG); CHECK(resize(1,~0u) == E_INVALIDARG);
        HR(device->Commit()); pixel(window.handle,2,2,background,255,background);

        // Keep the previous update texture alive beyond resize. Its dimensions
        // stay fixed and writes through it cannot modify the replacement image.
        Ref<ID3D11Texture2D> old_texture, texture;
        Ref<ID3D11RenderTargetView> old_view, view;
        POINT offset;
        HR(surface->BeginDraw(nullptr,__uuidof(ID3D11Texture2D),reinterpret_cast<void **>(&old_texture.p),&offset));
        HR(d3d->CreateRenderTargetView(old_texture.p,nullptr,&old_view.p));
        const float red[]{value,0,0,opacity};
        context->ClearRenderTargetView(old_view.p,red); HR(surface->EndDraw()); HR(device->Commit());
        HR(resize(5,4));
        pixel(window.handle,1,1,255,background,background); // pending resize preserves committed display
        RECT partial{1,1,2,2};
        CHECK(surface->BeginDraw(&partial,__uuidof(ID3D11Texture2D),reinterpret_cast<void **>(&texture.p),&offset)
              == E_INVALIDARG && !texture.p);
        HR(device->Commit()); pixel(window.handle,1,1,255,255,255); // content invalidated
        HR(surface->BeginDraw(nullptr,__uuidof(ID3D11Texture2D),reinterpret_cast<void **>(&texture.p),&offset));
        D3D11_TEXTURE2D_DESC desc = {}, old_desc = {};
        texture->GetDesc(&desc); old_texture->GetDesc(&old_desc);
        CHECK(desc.Width == 5 && desc.Height == 4 && desc.Format == format);
        CHECK(old_desc.Width == 3 && old_desc.Height == 3 && old_desc.Format == format);
        HR(d3d->CreateRenderTargetView(texture.p,nullptr,&view.p));
        const float blue[]{0,0,value,opacity};
        context->ClearRenderTargetView(view.p,blue); HR(surface->EndDraw());
        context->ClearRenderTargetView(old_view.p,red);
        HR(device->Commit()); pixel(window.handle,4,3,background,background,255);
        pixel(window.handle,1,1,background,background,255); pixel(window.handle,5,3,255,255,255);
        HR(fill(surface.p,d3d.p,context.p,&partial,0,value,0,opacity)); HR(device->Commit());
        pixel(window.handle,1,1,background,255,background); pixel(window.handle,4,3,background,background,255);
        texture.reset(); view.reset();

        // Equal-size resize also invalidates native logical-surface contents.
        HR(resize(5,4));
        CHECK(surface->BeginDraw(&partial,__uuidof(ID3D11Texture2D),reinterpret_cast<void **>(&texture.p),&offset)
              == E_INVALIDARG && !texture.p);
        HR(device->Commit()); pixel(window.handle,1,1,255,255,255);
        HR(fill(surface.p,d3d.p,context.p,nullptr,value,0,0,opacity)); HR(device->Commit());
        HR(resize(2,2)); HR(fill(surface.p,d3d.p,context.p,nullptr,0,value,0,opacity)); HR(device->Commit());
        pixel(window.handle,1,1,background,255,background); pixel(window.handle,4,3,255,255,255);

        // Another surface's draw does not prevent this surface from resizing.
        HR(fill(other.p,d3d.p,context.p,nullptr,0,0,value,opacity,false));
        HR(resize(4,5)); HR(other->EndDraw());
        HR(fill(surface.p,d3d.p,context.p,nullptr,0,0,value,opacity)); HR(device->Commit());
        pixel(window.handle,3,4,background,background,255);
        HR(resize(0,0)); HR(device->Commit()); pixel(window.handle,1,1,255,255,255);
        CHECK(surface->BeginDraw(nullptr,__uuidof(ID3D11Texture2D),reinterpret_cast<void **>(&texture.p),&offset)
              == static_cast<HRESULT>(0x8000000e) && !texture.p);
        HR(resize(3,3)); HR(fill(surface.p,d3d.p,context.p,nullptr,value,0,0,opacity)); HR(device->Commit());
        pixel(window.handle,2,2,255,background,background); pixel(window.handle,3,3,255,255,255);

        HR(root->SetContent(nullptr)); HR(device->Commit());
        public_alias.reset(); identity.reset(); partner_identity.reset(); surface.reset();
        // The partner alone still owns the surface and its renderer/device.
        HR(direct(partner.p,7,3));
    }
    HR(target->SetRoot(nullptr)); HR(consumer->Commit());
    HR(publisher->SetRoot(nullptr)); HR(device->Commit());
    pump();
    std::fprintf(stderr,"Logical surface resize: %s, discard/full redraw, active draw, failure atomicity, "
                        "BGRA/RGBA, alpha, COM lifetime and shared publication checked\n",
                 wrapped ? "native FrameworkUdk" : "direct partner ABI");
}
