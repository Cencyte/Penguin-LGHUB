/* Public D3D11 regression: preserve per-device creation flags and validate a
 * real render-target clear/copy/readback. Run headlessly on a private display.
 */
#define COBJMACROS
#include <windows.h>
#include <d3d11_3.h>
#include <stdio.h>
#include <stdlib.h>

static unsigned failures;
#define CHECK(test) do { if (!(test)) { fprintf(stderr, "FAIL line %u: %s\n", __LINE__, #test); ++failures; } } while (0)

static void readback(ID3D11Device *device, ID3D11DeviceContext *context)
{
    D3D11_TEXTURE2D_DESC desc = {0};
    D3D11_MAPPED_SUBRESOURCE mapped;
    ID3D11Texture2D *target = NULL, *staging = NULL;
    ID3D11RenderTargetView *view = NULL;
    const FLOAT color[] = {0.125f, 0.25f, 0.5f, 1.0f};
    const BYTE expected[] = {32, 64, 128, 255};
    HRESULT hr;
    unsigned x, y, c;
    desc.Width = desc.Height = 2;
    desc.MipLevels = desc.ArraySize = 1;
    desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    desc.SampleDesc.Count = 1;
    desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_RENDER_TARGET;
    hr = ID3D11Device_CreateTexture2D(device, &desc, NULL, &target);
    CHECK(SUCCEEDED(hr));
    if (FAILED(hr)) goto done;
    hr = ID3D11Device_CreateRenderTargetView(device, (ID3D11Resource *)target, NULL, &view);
    CHECK(SUCCEEDED(hr));
    if (FAILED(hr)) goto done;
    ID3D11DeviceContext_ClearRenderTargetView(context, view, color);
    desc.Usage = D3D11_USAGE_STAGING;
    desc.BindFlags = 0;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    hr = ID3D11Device_CreateTexture2D(device, &desc, NULL, &staging);
    CHECK(SUCCEEDED(hr));
    if (FAILED(hr)) goto done;
    ID3D11DeviceContext_CopyResource(context, (ID3D11Resource *)staging, (ID3D11Resource *)target);
    hr = ID3D11DeviceContext_Map(context, (ID3D11Resource *)staging, 0, D3D11_MAP_READ, 0, &mapped);
    CHECK(SUCCEEDED(hr));
    if (FAILED(hr)) goto done;
    for (y = 0; y < 2; ++y) for (x = 0; x < 2; ++x) for (c = 0; c < 4; ++c) {
        BYTE actual = ((BYTE *)mapped.pData)[y * mapped.RowPitch + 4 * x + c];
        CHECK(abs(actual - expected[c]) <= 1);
    }
    ID3D11DeviceContext_Unmap(context, (ID3D11Resource *)staging, 0);
done:
    if (view) ID3D11RenderTargetView_Release(view);
    if (staging) ID3D11Texture2D_Release(staging);
    if (target) ID3D11Texture2D_Release(target);
}

static void immediate_contexts(ID3D11Device *device, ID3D11DeviceContext *original)
{
    ID3D11Device3 *device3 = NULL;
    ID3D11DeviceContext2 *context2 = NULL;
    ID3D11DeviceContext3 *context3 = NULL;
    ID3D11DeviceContext *contexts[2];
    IUnknown *identity = NULL;
    ULONG before, after;
    HRESULT hr;
    unsigned i;
    hr = ID3D11Device_QueryInterface(device, &IID_ID3D11Device3, (void **)&device3);
    CHECK(SUCCEEDED(hr));
    if (!device3) return;
    before = ID3D11DeviceContext_AddRef(original);
    ID3D11DeviceContext_Release(original);
    ID3D11Device3_GetImmediateContext2(device3, &context2);
    ID3D11Device3_GetImmediateContext3(device3, &context3);
    CHECK(context2 != NULL && context3 != NULL);
    after = ID3D11DeviceContext_AddRef(original);
    ID3D11DeviceContext_Release(original);
    CHECK(after == before + 2);
    hr = ID3D11DeviceContext_QueryInterface(original, &IID_IUnknown, (void **)&identity);
    CHECK(SUCCEEDED(hr));
    contexts[0] = (ID3D11DeviceContext *)context2;
    contexts[1] = (ID3D11DeviceContext *)context3;
    for (i = 0; i < ARRAYSIZE(contexts); ++i) if (contexts[i]) {
        IUnknown *roundtrip = NULL;
        ID3D11Device *owner = NULL;
        hr = ID3D11DeviceContext_QueryInterface(contexts[i], &IID_IUnknown, (void **)&roundtrip);
        CHECK(SUCCEEDED(hr) && roundtrip == identity);
        if (roundtrip) IUnknown_Release(roundtrip);
        CHECK(ID3D11DeviceContext_GetType(contexts[i]) == D3D11_DEVICE_CONTEXT_IMMEDIATE);
        ID3D11DeviceContext_GetDevice(contexts[i], &owner);
        CHECK(owner == device);
        if (owner) ID3D11Device_Release(owner);
        readback(device, contexts[i]);
        ID3D11DeviceContext_Release(contexts[i]);
    }
    if (identity) IUnknown_Release(identity);
    after = ID3D11DeviceContext_AddRef(original);
    ID3D11DeviceContext_Release(original);
    CHECK(after == before);
    ID3D11Device3_Release(device3);
}

int main(void)
{
    const UINT flags[] = {0, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        D3D11_CREATE_DEVICE_SINGLETHREADED | D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        D3D11_CREATE_DEVICE_PREVENT_INTERNAL_THREADING_OPTIMIZATIONS | D3D11_CREATE_DEVICE_BGRA_SUPPORT};
    ID3D11Device *devices[4] = {0};
    unsigned i;
    setbuf(stderr, NULL);
    for (i = 0; i < ARRAYSIZE(flags); ++i) {
        ID3D11DeviceContext *context = NULL;
        HRESULT hr = D3D11CreateDevice(NULL, D3D_DRIVER_TYPE_HARDWARE, NULL, flags[i],
            NULL, 0, D3D11_SDK_VERSION, &devices[i], NULL, &context);
        CHECK(SUCCEEDED(hr) && devices[i] && context);
        if (FAILED(hr) || !devices[i]) continue;
        fprintf(stderr, "Device %u: requested=%#x returned=%#x\n", i, flags[i], ID3D11Device_GetCreationFlags(devices[i]));
        CHECK(ID3D11Device_GetCreationFlags(devices[i]) == flags[i]);
        {
            ID3D11Device1 *device1 = NULL;
            ID3DDeviceContextState *state = NULL;
            D3D_FEATURE_LEVEL level = ID3D11Device_GetFeatureLevel(devices[i]);
            hr = ID3D11Device_QueryInterface(devices[i], &IID_ID3D11Device1, (void **)&device1);
            CHECK(SUCCEEDED(hr));
            if (device1) {
                hr = ID3D11Device1_CreateDeviceContextState(device1, 0, &level, 1,
                    D3D11_SDK_VERSION, &IID_ID3D11Device, NULL, &state);
                CHECK(SUCCEEDED(hr));
                CHECK(ID3D11Device_GetCreationFlags(devices[i]) == flags[i]);
                if (state) ID3DDeviceContextState_Release(state);
                ID3D11Device1_Release(device1);
            }
        }
        if (context) {
            immediate_contexts(devices[i], context);
            readback(devices[i], context);
            ID3D11DeviceContext_Release(context);
        }
    }
    /* Keep devices alive together so a global last-flags workaround cannot pass. */
    for (i = 0; i < ARRAYSIZE(flags); ++i) if (devices[i]) {
        CHECK(ID3D11Device_GetCreationFlags(devices[i]) == flags[i]);
        ID3D11Device_Release(devices[i]);
    }
    fprintf(stderr, "D3D11 flags, context identity/lifetime and readback: %s (%u failures)\n", failures ? "FAIL" : "PASS", failures);
    return !!failures;
}
