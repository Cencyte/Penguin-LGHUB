/* Native ID2DPrivateInfo ABI: real per-device flush/present submission counts.
 * Run on Xvfb. This deliberately creates a small window to exercise DXGI Present.
 */
#define COBJMACROS
#include <windows.h>
#include <d3d11_1.h>
#include <d3d10_1.h>
#include <stdio.h>

typedef struct private_info private_info;
typedef struct {
    HRESULT (WINAPI *QueryInterface)(private_info *, REFIID, void **);
    ULONG (WINAPI *AddRef)(private_info *);
    ULONG (WINAPI *Release)(private_info *);
    UINT64 (WINAPI *ConservativeFlushCount)(private_info *);
} private_info_vtbl;
struct private_info { const private_info_vtbl *lpVtbl; };
static const GUID private_info_iid = {0xb79cc8da,0x337f,0x400f,{0xb0,0x9d,0xb2,0xed,0xf8,0xa8,0x4e,0x47}};
static unsigned failures;
#define CHECK(test) do { if (!(test)) { fprintf(stderr, "FAIL line %u: %s\n", __LINE__, #test); ++failures; } } while (0)
#define COUNT(info) ((info)->lpVtbl->ConservativeFlushCount(info))

int main(void)
{
    DXGI_SWAP_CHAIN_DESC desc = {0};
    ID3D11Device *device = NULL, *other = NULL;
    ID3D11DeviceContext *context = NULL, *deferred = NULL, *other_context = NULL;
    IDXGISwapChain *swapchain = NULL;
    ID3D10Device1 *device10 = NULL;
    private_info *info = NULL, *other_info = NULL, *info10 = NULL;
    IUnknown *identity = NULL, *roundtrip = NULL;
    HWND window;
    UINT64 before, other_before;
    HRESULT hr;
    HMODULE d3d10_module = NULL;
    HRESULT (WINAPI *create_device10)(IDXGIAdapter *, D3D10_DRIVER_TYPE, HMODULE, UINT,
        D3D10_FEATURE_LEVEL1, UINT, ID3D10Device1 **);
    setbuf(stderr, NULL);
    SetEnvironmentVariableA("GHUB_D3D11_PARTNER_PROBE", "1");
    window = CreateWindowExW(0, L"STATIC", L"Private D2D count test", WS_OVERLAPPEDWINDOW,
        0, 0, 64, 64, NULL, NULL, GetModuleHandleW(NULL), NULL);
    CHECK(window != NULL);
    if (!window) return 1;
    ShowWindow(window, SW_SHOWNOACTIVATE);
    desc.BufferDesc.Width = desc.BufferDesc.Height = 64;
    desc.BufferDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    desc.SampleDesc.Count = 1;
    desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    desc.BufferCount = 1;
    desc.OutputWindow = window;
    desc.Windowed = TRUE;
    desc.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;
    hr = D3D11CreateDeviceAndSwapChain(NULL, D3D_DRIVER_TYPE_HARDWARE, NULL,
        D3D11_CREATE_DEVICE_BGRA_SUPPORT, NULL, 0, D3D11_SDK_VERSION, &desc,
        &swapchain, &device, NULL, &context);
    CHECK(SUCCEEDED(hr));
    if (FAILED(hr)) goto done;
    hr = ID3D11Device_QueryInterface(device, &private_info_iid, (void **)&info);
    CHECK(SUCCEEDED(hr) && info);
    if (!info) goto done;
    CHECK(info->lpVtbl->QueryInterface(info, &IID_IUnknown, NULL) == E_POINTER);
    hr = ID3D11Device_QueryInterface(device, &IID_IUnknown, (void **)&identity);
    CHECK(SUCCEEDED(hr));
    hr = info->lpVtbl->QueryInterface(info, &IID_IUnknown, (void **)&roundtrip);
    CHECK(SUCCEEDED(hr) && roundtrip == identity);
    if (roundtrip) { IUnknown_Release(roundtrip); roundtrip = NULL; }
    if (identity) { IUnknown_Release(identity); identity = NULL; }

    before = COUNT(info);
    ID3D11DeviceContext_Flush(context);
    CHECK(COUNT(info) == before + 1);
    before = COUNT(info);
    CHECK(COUNT(info) == before);
    hr = ID3D11Device_CreateDeferredContext(device, 0, &deferred);
    CHECK(SUCCEEDED(hr));
    before = COUNT(info);
    if (deferred) ID3D11DeviceContext_Flush(deferred);
    CHECK(COUNT(info) == before);
    hr = IDXGISwapChain_Present(swapchain, 0, DXGI_PRESENT_TEST);
    CHECK(hr == S_OK && COUNT(info) == before);
    hr = IDXGISwapChain_Present(swapchain, 5, 0);
    CHECK(hr == DXGI_ERROR_INVALID_CALL && COUNT(info) == before);
    hr = IDXGISwapChain_Present(swapchain, 0, 0);
    CHECK(hr == S_OK && COUNT(info) == before + 1);

    hr = D3D11CreateDevice(NULL, D3D_DRIVER_TYPE_HARDWARE, NULL, 0, NULL, 0,
        D3D11_SDK_VERSION, &other, NULL, &other_context);
    CHECK(SUCCEEDED(hr));
    if (other) {
        hr = ID3D11Device_QueryInterface(other, &private_info_iid, (void **)&other_info);
        CHECK(SUCCEEDED(hr) && other_info);
    }
    if (other_info && other_context) {
        before = COUNT(info); other_before = COUNT(other_info);
        ID3D11DeviceContext_Flush(other_context);
        CHECK(COUNT(other_info) == other_before + 1 && COUNT(info) == before);
    }
    d3d10_module = LoadLibraryW(L"d3d10_1.dll");
    CHECK(d3d10_module != NULL);
    if (!d3d10_module) goto done;
    create_device10 = (void *)GetProcAddress(d3d10_module, "D3D10CreateDevice1");
    CHECK(create_device10 != NULL);
    if (!create_device10) goto done;
    hr = create_device10(NULL, D3D10_DRIVER_TYPE_HARDWARE, NULL, 0,
        D3D10_FEATURE_LEVEL_10_1, D3D10_1_SDK_VERSION, &device10);
    CHECK(SUCCEEDED(hr));
    if (device10) {
        hr = ID3D10Device1_QueryInterface(device10, &private_info_iid, (void **)&info10);
        CHECK(SUCCEEDED(hr) && info10);
        if (info10) {
            before = COUNT(info10);
            ID3D10Device1_Flush(device10);
            CHECK(COUNT(info10) == before + 1);
        }
    }
done:
    if (info10) info10->lpVtbl->Release(info10);
    if (device10) ID3D10Device1_Release(device10);
    if (d3d10_module) FreeLibrary(d3d10_module);
    if (other_context) ID3D11DeviceContext_Release(other_context);
    if (other) ID3D11Device_Release(other);
    if (other_info) { COUNT(other_info); other_info->lpVtbl->Release(other_info); }
    if (deferred) ID3D11DeviceContext_Release(deferred);
    if (swapchain) IDXGISwapChain_Release(swapchain);
    if (context) ID3D11DeviceContext_Release(context);
    if (device) ID3D11Device_Release(device);
    if (info) { COUNT(info); info->lpVtbl->Release(info); }
    DestroyWindow(window);
    fprintf(stderr, "D2D private flush counter: %s (%u failures)\n", failures ? "FAIL" : "PASS", failures);
    return !!failures;
}
