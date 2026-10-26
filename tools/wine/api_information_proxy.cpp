/* Per-prefix Wine 10.20 compatibility bridge, not a replacement WinRT runtime.
 * G HUB queries LampArray.IsAvailable before enabling Windows Dynamic Lighting.
 * Wine has no LampArray implementation; report this property absent, while
 * forwarding every other query to the installed Wine wintypes implementation.
 * Register this DLL only as the ApiInformation activation factory in a copied
 * prefix. No system Wine files or G HUB binaries need modification.
 */
#include <windows.h>
#include <roapi.h>
#include <winstring.h>
#include "api_information_abi.h"
#include <new>
#include <wchar.h>

class Factory final : public IActivationFactory, public IApiInformationStatics {
    LONG refs = 1;
    IApiInformationStatics *real;
    IActivationFactory *factory;
public:
    Factory(IActivationFactory *f, IApiInformationStatics *value) : real(value), factory(f) {}
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void **out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid == __uuidof(IApiInformationStatics))
            *out = static_cast<IApiInformationStatics *>(this);
        else if (iid == __uuidof(IUnknown) || iid == __uuidof(IInspectable) ||
                 iid == __uuidof(IAgileObject) || iid == __uuidof(IActivationFactory))
            *out = static_cast<IActivationFactory *>(this);
        else return E_NOINTERFACE;
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&refs); }
    ULONG STDMETHODCALLTYPE Release() override {
        ULONG count = InterlockedDecrement(&refs);
        if (!count) { real->Release(); factory->Release(); delete this; }
        return count;
    }
    HRESULT STDMETHODCALLTYPE GetIids(ULONG *n, IID **v) override { return real->GetIids(n, v); }
    HRESULT STDMETHODCALLTYPE GetRuntimeClassName(HSTRING *v) override { return real->GetRuntimeClassName(v); }
    HRESULT STDMETHODCALLTYPE GetTrustLevel(TrustLevel *v) override { return real->GetTrustLevel(v); }
    HRESULT STDMETHODCALLTYPE ActivateInstance(IInspectable **v) override { return factory->ActivateInstance(v); }
    HRESULT STDMETHODCALLTYPE IsTypePresent(HSTRING t, boolean *v) override { return real->IsTypePresent(t, v); }
    HRESULT STDMETHODCALLTYPE IsMethodPresent(HSTRING t, HSTRING m, boolean *v) override { return real->IsMethodPresent(t, m, v); }
    HRESULT STDMETHODCALLTYPE IsMethodPresentWithArity(HSTRING t, HSTRING m, UINT32 n, boolean *v) override { return real->IsMethodPresentWithArity(t, m, n, v); }
    HRESULT STDMETHODCALLTYPE IsEventPresent(HSTRING t, HSTRING e, boolean *v) override { return real->IsEventPresent(t, e, v); }
    HRESULT STDMETHODCALLTYPE IsPropertyPresent(HSTRING t, HSTRING p, boolean *v) override {
        if (!v) return E_POINTER;
        if (t && p && !wcscmp(WindowsGetStringRawBuffer(t, nullptr), L"Windows.Devices.Lights.LampArray") &&
            !wcscmp(WindowsGetStringRawBuffer(p, nullptr), L"IsAvailable")) {
            *v = false;
            return S_OK;
        }
        return real->IsPropertyPresent(t, p, v);
    }
    HRESULT STDMETHODCALLTYPE IsReadOnlyPropertyPresent(HSTRING t, HSTRING p, boolean *v) override { return real->IsReadOnlyPropertyPresent(t, p, v); }
    HRESULT STDMETHODCALLTYPE IsWriteablePropertyPresent(HSTRING t, HSTRING p, boolean *v) override { return real->IsWriteablePropertyPresent(t, p, v); }
    HRESULT STDMETHODCALLTYPE IsEnumNamedValuePresent(HSTRING t, HSTRING n, boolean *v) override { return real->IsEnumNamedValuePresent(t, n, v); }
    HRESULT STDMETHODCALLTYPE IsApiContractPresentByMajor(HSTRING t, UINT16 n, boolean *v) override { return real->IsApiContractPresentByMajor(t, n, v); }
    HRESULT STDMETHODCALLTYPE IsApiContractPresentByMajorAndMinor(HSTRING t, UINT16 n, UINT16 m, boolean *v) override { return real->IsApiContractPresentByMajorAndMinor(t, n, m, v); }
};

extern "C" __declspec(dllexport) HRESULT WINAPI DllGetActivationFactory(HSTRING name, IActivationFactory **out) {
    if (!out) return E_POINTER;
    *out = nullptr;
    // Keep the backing module loaded for the lifetime of all returned objects.
    static HMODULE module = LoadLibraryExW(L"wintypes.dll", nullptr, LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (!module) return HRESULT_FROM_WIN32(GetLastError());
    using GetFactory = HRESULT (WINAPI *)(HSTRING, IActivationFactory **);
    auto get = reinterpret_cast<GetFactory>(GetProcAddress(module, "DllGetActivationFactory"));
    if (!get) return HRESULT_FROM_WIN32(ERROR_PROC_NOT_FOUND);
    IActivationFactory *real = nullptr;
    HRESULT hr = get(name, &real);
    if (FAILED(hr)) return hr;
    if (wcscmp(WindowsGetStringRawBuffer(name, nullptr), L"Windows.Foundation.Metadata.ApiInformation")) {
        *out = real;
        return S_OK;
    }
    IApiInformationStatics *statics = nullptr;
    hr = real->QueryInterface(__uuidof(IApiInformationStatics), reinterpret_cast<void **>(&statics));
    if (FAILED(hr)) { real->Release(); return hr; }
    auto *proxy = new (std::nothrow) Factory(real, statics);
    if (!proxy) { statics->Release(); real->Release(); return E_OUTOFMEMORY; }
    *out = proxy;
    return S_OK;
}

// Objects forward into Wine; retain this small bridge until process shutdown.
extern "C" __declspec(dllexport) HRESULT WINAPI DllCanUnloadNow() { return S_FALSE; }
