// End-to-end pixel checks, including inherited modes and shared frame bounds.
static void sampling_run(HMODULE module)
{
    SharedApi api(module);
    if (!api.device || !api.shared || !api.target) return;
    Ref<ID3D11Device> d3d;
    Ref<ID3D11DeviceContext> context;
    Ref<IDXGIDevice> dxgi;
    Ref<IDCompositionDesktopDevice> device, consumer;
    Ref<IDCompositionSurface> surface, blue;
    Ref<IDCompositionVisual2> root, leaf, group;
    Ref<IDCompositionTarget> target;
    HR(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                         nullptr, 0, D3D11_SDK_VERSION, &d3d.p, nullptr, &context.p));
    HR(d3d->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void **>(&dxgi.p)));
    HR(api.device(dxgi.p, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&device.p)));
    HR(device->CreateSurface(2, 2, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_ALPHA_MODE_PREMULTIPLIED, &surface.p));
    HR(device->CreateSurface(2, 2, DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_ALPHA_MODE_PREMULTIPLIED, &blue.p));
    HR(fill(surface.p, d3d.p, context.p, nullptr, 1, 0, 0, 1));
    RECT green{1, 0, 2, 1}, lower_blue{0, 1, 1, 2}, white{1, 1, 2, 2};
    HR(fill(surface.p, d3d.p, context.p, &green, 0, 1, 0, 1));
    HR(fill(surface.p, d3d.p, context.p, &lower_blue, 0, 0, 1, 1));
    HR(fill(surface.p, d3d.p, context.p, &white, 1, 1, 1, 1));
    HR(fill(blue.p, d3d.p, context.p, nullptr, 0, 0, 1, 1));
    HR(device->CreateVisual(&root.p));
    HR(device->CreateVisual(&leaf.p));
    HR(device->CreateVisual(&group.p));
    HR(root->SetOffsetX(16.0f));
    HR(root->SetOffsetY(16.0f));
    HR(root->AddVisual(leaf.p, TRUE, nullptr));
    HR(leaf->SetContent(surface.p));
    HR(leaf->SetOffsetX(.5f));
    HR(leaf->SetOffsetY(.5f));
    Window window;
    CHECK(window.handle);
    if (!window.handle) return;
    pump();
    SetEnvironmentVariableW(L"GHUB_DCOMP_POPUP_TARGET", L"1");
    HR(device->CreateTargetForHwnd(window.handle, TRUE, &target.p));
    HR(target->SetRoot(root.p));
    HR(device->Commit());
    pixel(window.handle, 1, 1, 255, 255, 255); // default nearest selects white texel
    pixel(window.handle, 0, 0, 255, 0, 0); // default hard border
    HR(root->SetBitmapInterpolationMode(DCOMPOSITION_BITMAP_INTERPOLATION_MODE_LINEAR));
    HR(root->SetBorderMode(DCOMPOSITION_BORDER_MODE_SOFT));
    pixel(window.handle, 1, 1, 255, 255, 255); // no publication before Commit
    HR(device->Commit());
    pixel(window.handle, 1, 1, 128, 128, 128); // bilinear average of four different texels
    pixel(window.handle, 0, 0, 255, 191, 191); // quarter-pixel geometric coverage
    pixel(window.handle, 0, 1, 191, 127, 191); // filtered purple, half coverage
    HR(leaf->SetBitmapInterpolationMode(DCOMPOSITION_BITMAP_INTERPOLATION_MODE_NEAREST_NEIGHBOR));
    HR(device->Commit());
    pixel(window.handle, 1, 1, 255, 255, 255); // explicit override
    HR(leaf->SetBitmapInterpolationMode(DCOMPOSITION_BITMAP_INTERPOLATION_MODE_INHERIT));
    HR(device->Commit());
    pixel(window.handle, 1, 1, 128, 128, 128);
    CHECK(root->SetBitmapInterpolationMode(static_cast<DCOMPOSITION_BITMAP_INTERPOLATION_MODE>(2)) == E_INVALIDARG);
    CHECK(root->SetBorderMode(static_cast<DCOMPOSITION_BORDER_MODE>(2)) == E_INVALIDARG);
    CHECK(root->SetBackFaceVisibility(static_cast<DCOMPOSITION_BACKFACE_VISIBILITY>(2)) == E_INVALIDARG);
    CHECK(leaf->SetOffsetX(std::numeric_limits<float>::quiet_NaN()) == E_INVALIDARG);
    CHECK(leaf->SetOffsetY(std::numeric_limits<float>::infinity()) == E_INVALIDARG);
    HR(device->Commit());
    pixel(window.handle, 1, 1, 128, 128, 128); // rejected setters preserve state
    HR(group->SetBitmapInterpolationMode(DCOMPOSITION_BITMAP_INTERPOLATION_MODE_NEAREST_NEIGHBOR));
    HR(root->RemoveVisual(leaf.p));
    HR(group->AddVisual(leaf.p, TRUE, nullptr));
    HR(root->AddVisual(group.p, TRUE, nullptr));
    HR(device->Commit());
    pixel(window.handle, 1, 1, 255, 255, 255); // reparenting changes inherited state
    HR(group->SetBitmapInterpolationMode(DCOMPOSITION_BITMAP_INTERPOLATION_MODE_INHERIT));
    HR(root->SetBackFaceVisibility(DCOMPOSITION_BACKFACE_VISIBILITY_HIDDEN));
    HR(device->Commit());
    pixel(window.handle, 1, 1, 128, 128, 128); // HIDDEN does not hide front faces
    HR(leaf->SetBackFaceVisibility(DCOMPOSITION_BACKFACE_VISIBILITY_VISIBLE));
    HR(device->Commit());
    pixel(window.handle, 1, 1, 128, 128, 128);
    HR(leaf->SetBackFaceVisibility(DCOMPOSITION_BACKFACE_VISIBILITY_INHERIT));
    HR(root->SetBackFaceVisibility(DCOMPOSITION_BACKFACE_VISIBILITY_INHERIT));
    // A soft clip masks the composed group once, including overlapping layers.
    HR(fill(surface.p, d3d.p, context.p, nullptr, 1, 0, 0, 1));
    HR(root->SetContent(surface.p));
    HR(leaf->SetContent(blue.p));
    HR(leaf->SetOffsetX(0.0f));
    HR(leaf->SetOffsetY(0.0f));
    D2D_RECT_F clip{.5f, 0, 2, 2};
    HR(root->SetClip(clip));
    HR(device->Commit());
    pixel(window.handle, 0, 1, 127, 127, 255); // no red leaks from covered lower layer
    pixel(window.handle, 1, 1, 0, 0, 255);
    D2D_RECT_F inner_clip{0, .5f, 2, 2};
    HR(group->SetClip(inner_clip));
    HR(device->Commit());
    pixel(window.handle, 0, 0, 191, 127, 191); // nested masks use their own local layer origins
    pixel(window.handle, 1, 0, 127, 0, 128);
    HR(group->SetClip(static_cast<IDCompositionClip *>(nullptr)));
    HR(root->SetBorderMode(DCOMPOSITION_BORDER_MODE_HARD));
    HR(device->Commit());
    pixel(window.handle, 0, 1, 0, 0, 255);
    HR(root->SetClip(static_cast<IDCompositionClip *>(nullptr)));
    HR(root->SetContent(nullptr));
    HR(leaf->SetContent(surface.p));
    HR(root->SetBorderMode(DCOMPOSITION_BORDER_MODE_SOFT));
    HR(leaf->SetOffsetX(-.5f));
    HR(device->Commit());
    pixel(window.handle, -1, 0, 255, 127, 127); // negative fractional origin uses floor, not truncation
    HR(fill(surface.p, d3d.p, context.p, nullptr, .5f, 0, 0, .5f));
    HR(device->Commit());
    pixel(window.handle, -1, 0, 255, 191, 191); // premultiplied colour and alpha receive equal coverage
    HR(fill(surface.p, d3d.p, context.p, nullptr, 1, 0, 0, 1));
    HR(leaf->SetOffsetX(.5f));
    HR(device->Commit());
    pixel(window.handle, 2, 0, 255, 127, 127);
    HR(target->SetRoot(nullptr));
    HR(device->Commit());
    target.reset();
    Handle handle;
    Ref<IDCompositionTarget> publisher, destination;
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
    pixel(window.handle, 2, 0, 255, 127, 127); // ceil extent preserves the final partially covered column
    HR(imported->SetBitmapInterpolationMode(DCOMPOSITION_BITMAP_INTERPOLATION_MODE_LINEAR));
    HR(imported->SetOffsetX(.5f));
    HR(consumer->Commit());
    pixel(window.handle, 1, 0, 255, 63, 63); // imported frames use the same sampler
    HR(destination->SetRoot(nullptr));
    HR(consumer->Commit());
    HR(publisher->SetRoot(nullptr));
    HR(device->Commit());
    std::fputs("Sampling, inherited modes and subtree border coverage checked\n", stderr);
}
