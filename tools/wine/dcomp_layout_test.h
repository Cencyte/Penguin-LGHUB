// Real pixel tests for the private visual layout ABI, using the native
// FrameworkUdk size wrappers when --framework is enabled in the runner.
static const IID layout_iid = {0x2c4eef28, 0x1bc0, 0x4736, {0xb7, 0xdd, 0xb6, 0x26, 0x92, 0xf9, 0xbd, 0x67}};
struct LayoutApi
{
    using Pair = HRESULT(WINAPI *)(IUnknown *, float, float);
    Pair fixed = nullptr, relative = nullptr;
    explicit LayoutApi(const SharedApi &api)
    {
        if (api.framework)
        {
            fixed = reinterpret_cast<Pair>(reinterpret_cast<void *>(GetProcAddress(api.framework, "DCompPrivates_SetVisualSize")));
            relative = reinterpret_cast<Pair>(reinterpret_cast<void *>(GetProcAddress(api.framework, "DCompPrivates_SetVisualRelativeSize")));
            CHECK(fixed && relative);
        }
    }
    static HRESULT property(IUnknown *visual, unsigned slot, float value)
    {
        Ref<IUnknown> restricted;
        HRESULT hr = visual->QueryInterface(layout_iid, reinterpret_cast<void **>(&restricted.p));
        if (FAILED(hr)) return hr;
        using Set = HRESULT(WINAPI *)(IUnknown *, float);
        auto method = reinterpret_cast<Set>((*reinterpret_cast<void ***>(restricted.p))[slot]);
        return method(restricted.p, value);
    }
    HRESULT size(IUnknown *visual, float width, float height, bool is_relative = false)
    {
        Pair method = is_relative ? relative : fixed;
        if (method) return method(visual, width, height);
        unsigned slot = is_relative ? 40 : 36;
        HRESULT hr = property(visual, slot, width);
        return FAILED(hr) ? hr : property(visual, slot + 1, height);
    }
};

static void layout_run(HMODULE module)
{
    SharedApi api(module);
    LayoutApi layout(api);
    if (!api.device || !api.shared || !api.target || (api.framework && (!layout.fixed || !layout.relative))) return;
    Ref<ID3D11Device> d3d;
    Ref<ID3D11DeviceContext> context;
    Ref<IDXGIDevice> dxgi;
    Ref<IDCompositionDesktopDevice> device, consumer;
    Ref<IDCompositionSurface> surface;
    Ref<IDCompositionVisual2> root, group, leaf, other;
    Ref<IDCompositionTarget> target;
    HR(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                         nullptr, 0, D3D11_SDK_VERSION, &d3d.p, nullptr, &context.p));
    HR(d3d->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void **>(&dxgi.p)));
    HR(api.device(dxgi.p, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&device.p)));
    HR(device->CreateSurface(4, 4, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_ALPHA_MODE_PREMULTIPLIED, &surface.p));
    HR(device->CreateVisual(&root.p));
    HR(device->CreateVisual(&group.p));
    HR(device->CreateVisual(&leaf.p));
    HR(device->CreateVisual(&other.p));
    HR(root->AddVisual(group.p, TRUE, nullptr));
    HR(group->AddVisual(leaf.p, TRUE, nullptr));
    HR(leaf->SetContent(surface.p));
    HR(root->SetOffsetX(16.0f));
    HR(root->SetOffsetY(16.0f));
    HR(layout.size(root.p, 20, 12));
    HR(layout.size(root.p, 9, 9, true)); // no parent: relative root size contributes zero
    HR(layout.size(group.p, 2, 2));
    HR(layout.size(group.p, .5f, .5f, true));
    HR(LayoutApi::property(leaf.p, 38, 1));
    HR(LayoutApi::property(leaf.p, 39, 1));
    Ref<IUnknown> public_identity, private_identity, restricted;
    HR(leaf->QueryInterface(__uuidof(IUnknown), reinterpret_cast<void **>(&public_identity.p)));
    HR(leaf->QueryInterface(layout_iid, reinterpret_cast<void **>(&restricted.p)));
    HR(restricted->QueryInterface(__uuidof(IUnknown), reinterpret_cast<void **>(&private_identity.p)));
    CHECK(public_identity.p == private_identity.p);
    Window window;
    CHECK(window.handle);
    if (!window.handle) return;
    SetEnvironmentVariableW(L"GHUB_DCOMP_POPUP_TARGET", L"1");
    HR(device->CreateTargetForHwnd(window.handle, TRUE, &target.p));
    HR(target->SetRoot(root.p));
    HR(fill(surface.p, d3d.p, context.p, nullptr, 1, 0, 0, 1));
    pixel(window.handle, 13, 9, 255, 255, 255);
    HR(device->Commit());
    pixel(window.handle, 13, 9, 255, 0, 0); // group effective size = (12,8)
    HR(layout.size(root.p, 28, 20));
    pixel(window.handle, 13, 9, 255, 0, 0); // setters do not publish before Commit
    pixel(window.handle, 17, 13, 255, 255, 255);
    HR(device->Commit());
    pixel(window.handle, 13, 9, 255, 255, 255);
    pixel(window.handle, 17, 13, 255, 0, 0); // descendants use updated (16,12)
    HR(layout.size(group.p, 1, .5f, true));
    HR(device->Commit());
    pixel(window.handle, 31, 13, 255, 0, 0);
    HR(layout.size(group.p, -40, -30));
    HR(device->Commit());
    pixel(window.handle, 1, 1, 255, 0, 0); // effective negative size clamps to zero
    pixel(window.handle, 31, 13, 255, 255, 255);
    // Logical zero size must not clip the ordinary 4x4 surface or descendants.
    HR(layout.size(group.p, 2, 2));
    HR(layout.size(group.p, .5f, .5f, true));
    CHECK(layout.size(group.p, std::numeric_limits<float>::quiet_NaN(), 2) == E_INVALIDARG);
    CHECK(layout.size(group.p, std::numeric_limits<float>::infinity(), 1, true) == E_INVALIDARG);
    CHECK(LayoutApi::property(leaf.p, 39, -std::numeric_limits<float>::infinity()) == E_INVALIDARG);
    HR(device->Commit());
    pixel(window.handle, 17, 13, 255, 0, 0); // rejected setters preserve prior state
    HR(layout.size(group.p, 1.25f, 2));
    HR(device->Commit()); // fractional final translations now use the inherited sampling mode
    pixel(window.handle, 16, 13, 255, 0, 0);
    HR(layout.size(group.p, 2, 2));
    HR(layout.size(group.p, std::numeric_limits<float>::max(), 1, true));
    CHECK(device->Commit() == E_INVALIDARG); // finite operands may overflow in layout
    HR(layout.size(group.p, .5f, .5f, true));
    HR(layout.size(other.p, 40, 20));
    HR(root->RemoveVisual(group.p));
    HR(other->AddVisual(group.p, TRUE, nullptr));
    HR(root->AddVisual(other.p, TRUE, nullptr));
    HR(device->Commit());
    pixel(window.handle, 17, 13, 255, 255, 255);
    pixel(window.handle, 23, 13, 255, 0, 0); // reparenting changes effective size

    // The same layout must feed shared-target extent and actual frame pixels.
    HR(target->SetRoot(nullptr));
    HR(device->Commit());
    target.reset();
    HR(root->RemoveVisual(other.p));
    Handle shared;
    Ref<IDCompositionTarget> publisher, destination;
    Ref<IDCompositionVisual> imported;
    HR(api.shared(&shared.value));
    HR(api.target(shared.value, &publisher.p));
    HR(publisher->SetRoot(other.p));
    HR(api.device(nullptr, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&consumer.p)));
    HR(api.open(consumer.p, shared.value, shared_visual_iid, reinterpret_cast<void **>(&imported.p)));
    shared.reset();
    HR(imported->SetOffsetX(16.0f));
    HR(imported->SetOffsetY(16.0f));
    HR(consumer->CreateTargetForHwnd(window.handle, TRUE, &destination.p));
    HR(destination->SetRoot(imported.p));
    HR(device->Commit());
    HR(consumer->Commit());
    pixel(window.handle, 23, 13, 255, 0, 0);
    HR(layout.size(other.p, 60, 28));
    HR(consumer->Commit());
    pixel(window.handle, 23, 13, 255, 0, 0);
    HR(device->Commit());
    HR(consumer->Commit());
    pixel(window.handle, 23, 13, 255, 255, 255);
    pixel(window.handle, 33, 17, 255, 0, 0);
    HR(destination->SetRoot(nullptr));
    HR(consumer->Commit());
    std::fputs("Relative visual size/offset and shared publication: PASS\n", stderr);
}
