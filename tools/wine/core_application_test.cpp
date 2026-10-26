// Standalone tests for the LIMITED diagnostic factory, not proof that native
// unhandled-error dispatch or CoreApplication lifecycle works.
#include <windows.h>
#include <roapi.h>
#include <winstring.h>
#include <cstdio>
#include <cwchar>
#include "core_application_private_abi.h"

static int failures;
#define CHECK(test) do { if (!(test)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #test); ++failures; } } while (0)
class Handler final : public IUnknown {
public:
    LONG refs = 1;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void **out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid != __uuidof(IUnknown) && iid != __uuidof(IAgileObject)) return E_NOINTERFACE;
        *out = this; AddRef(); return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&refs); }
    ULONG STDMETHODCALLTYPE Release() override { return InterlockedDecrement(&refs); }
};
int main() {
    CHECK(SUCCEEDED(RoInitialize(RO_INIT_MULTITHREADED)));
    auto module = LoadLibraryW(L"core_application_experiment.dll");
    CHECK(module != nullptr);
    if (!module) return 1;
    using GetFactory = HRESULT (WINAPI *)(HSTRING, IActivationFactory **);
    auto get = reinterpret_cast<GetFactory>(GetProcAddress(module, "DllGetActivationFactory"));
    CHECK(get != nullptr);
    if (!get) return 1;
    HSTRING name = nullptr;
    const wchar_t *text = L"Windows.ApplicationModel.Core.CoreApplication";
    CHECK(WindowsCreateString(text, wcslen(text), &name) == S_OK);
    IActivationFactory *factory = nullptr;
    CHECK(get(name, &factory) == S_OK);
    if (!factory) return 1;
    ICoreApplicationPrivate *priv = nullptr;
    CHECK(factory->QueryInterface(__uuidof(ICoreApplicationPrivate), reinterpret_cast<void **>(&priv)) == S_OK);
    if (!priv) return 1;
    IUnknown *identity1 = nullptr, *identity2 = nullptr;
    CHECK(factory->QueryInterface(__uuidof(IUnknown), reinterpret_cast<void **>(&identity1)) == S_OK);
    CHECK(priv->QueryInterface(__uuidof(IUnknown), reinterpret_cast<void **>(&identity2)) == S_OK);
    CHECK(identity1 == identity2);
    if (identity1) identity1->Release();
    if (identity2) identity2->Release();
    HSTRING actual = nullptr;
    CHECK(priv->GetRuntimeClassName(&actual) == S_OK);
    CHECK(!wcscmp(WindowsGetStringRawBuffer(actual, nullptr), text));
    WindowsDeleteString(actual);
    ULONG count = 0; IID *ids = nullptr;
    CHECK(priv->GetIids(&count, &ids) == S_OK && count == 2);
    if (ids) { CHECK(ids[1] == __uuidof(ICoreApplicationPrivate)); CoTaskMemFree(ids); }
    IInspectable *unsupported = reinterpret_cast<IInspectable *>(1);
    CHECK(factory->ActivateInstance(&unsupported) == E_NOTIMPL && !unsupported);
    Handler handler;
    CoreEventToken a{}, b{};
    CHECK(priv->add_FinalUnhandledErrorDetected(nullptr, &a) == E_INVALIDARG && !a.value);
    CHECK(priv->add_FinalUnhandledErrorDetected(&handler, nullptr) == E_POINTER);
    CHECK(handler.refs == 1);
    CHECK(priv->add_FinalUnhandledErrorDetected(&handler, &a) == S_OK && a.value);
    CHECK(priv->add_FinalUnhandledErrorDetected(&handler, &b) == S_OK && b.value && a.value != b.value);
    CHECK(handler.refs > 1);
    // Releasing every external factory reference must not lose subscriptions.
    priv->Release(); factory->Release();
    CHECK(get(name, &factory) == S_OK);
    CHECK(factory->QueryInterface(__uuidof(ICoreApplicationPrivate), reinterpret_cast<void **>(&priv)) == S_OK);
    CHECK(priv->remove_FinalUnhandledErrorDetected(a) == S_OK);
    LONG held = handler.refs;
    CHECK(held > 1);
    CHECK(priv->remove_FinalUnhandledErrorDetected(a) == S_OK && handler.refs == held);
    CHECK(priv->remove_FinalUnhandledErrorDetected({-1}) == S_OK && handler.refs == held);
    CHECK(priv->remove_FinalUnhandledErrorDetected(b) == S_OK && handler.refs == 1);
    CoreEventToken c{};
    CHECK(priv->add_FinalUnhandledErrorDetected(&handler, &c) == S_OK && c.value != a.value && c.value != b.value);
    CHECK(priv->remove_FinalUnhandledErrorDetected(a) == S_OK && handler.refs > 1);
    CHECK(priv->remove_FinalUnhandledErrorDetected(c) == S_OK && handler.refs == 1);
    priv->Release(); factory->Release(); WindowsDeleteString(name);
    RoUninitialize();
    printf("CoreApplication diagnostic factory: %s (%d failures); error dispatch is not implemented\n", failures ? "FAIL" : "PASS", failures);
    return !!failures;
}
