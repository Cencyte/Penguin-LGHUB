// Diagnostic compatibility experiment. Register ONLY in a disposable prefix.
// Implements the observed private event-registration ABI, not CoreApplication
// lifecycle/view activation. Wine's unhandled-error reporting is not connected
// to these subscriptions yet, so this is NOT a complete event implementation.
#include <windows.h>
#include <roapi.h>
#include <winstring.h>
#include <new>
#include <cstdio>
#include <cwchar>
#include <cstdint>
#include "core_application_private_abi.h"

static constexpr wchar_t class_name[] = L"Windows.ApplicationModel.Core.CoreApplication";
class Factory final : public IActivationFactory, public ICoreApplicationPrivate {
    struct Subscription { UINT64 token; IAgileReference *handler; Subscription *next; };
    LONG refs = 1; // Process lifetime anchor: class state survives factory release.
    SRWLOCK lock = SRWLOCK_INIT;
    UINT64 next_token = 0;
    Subscription *subscriptions = nullptr;
    template<typename T> HRESULT unsupported(const char *method, T *out) {
        if (!out) return E_POINTER;
        *out = {};
        fprintf(stderr, "core_application_experiment: unsupported %s\n", method);
        return E_NOTIMPL;
    }
public:
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void **out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid == __uuidof(ICoreApplicationPrivate))
            *out = static_cast<ICoreApplicationPrivate *>(this);
        else if (iid == __uuidof(IUnknown) || iid == __uuidof(IInspectable) ||
                 iid == __uuidof(IAgileObject) || iid == __uuidof(IActivationFactory))
            *out = static_cast<IActivationFactory *>(this);
        else {
            fprintf(stderr, "core_application_experiment: unsupported IID %08lx-%04x-%04x\n",
                    iid.Data1, iid.Data2, iid.Data3);
            return E_NOINTERFACE;
        }
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&refs); }
    ULONG STDMETHODCALLTYPE Release() override { return InterlockedDecrement(&refs); }
    HRESULT STDMETHODCALLTYPE GetIids(ULONG *count, IID **ids) override {
        if (!count || !ids) return E_POINTER;
        *count = 0; *ids = nullptr;
        auto *values = static_cast<IID *>(CoTaskMemAlloc(2 * sizeof(IID)));
        if (!values) return E_OUTOFMEMORY;
        values[0] = __uuidof(IActivationFactory); values[1] = __uuidof(ICoreApplicationPrivate);
        *ids = values; *count = 2;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetRuntimeClassName(HSTRING *value) override {
        if (!value) return E_POINTER;
        return WindowsCreateString(class_name, ARRAYSIZE(class_name) - 1, value);
    }
    HRESULT STDMETHODCALLTYPE GetTrustLevel(TrustLevel *value) override {
        if (!value) return E_POINTER;
        *value = BaseTrust; return S_OK;
    }
    HRESULT STDMETHODCALLTYPE ActivateInstance(IInspectable **value) override {
        return unsupported("ActivateInstance", value);
    }
    HRESULT STDMETHODCALLTYPE get_Context(IInspectable **v) override { return unsupported("get_Context", v); }
    HRESULT STDMETHODCALLTYPE get_PrivateProperties(IInspectable **v) override { return unsupported("get_PrivateProperties", v); }
    HRESULT STDMETHODCALLTYPE GetWindowFromThreadId(UINT32, IInspectable **v) override { return unsupported("GetWindowFromThreadId", v); }
    HRESULT STDMETHODCALLTYPE GetViewFromThreadId(UINT32, IInspectable **v) override { return unsupported("GetViewFromThreadId", v); }
    HRESULT STDMETHODCALLTYPE GetAppDisplayName(HSTRING *v) override { return unsupported("GetAppDisplayName", v); }
    HRESULT STDMETHODCALLTYPE add_FinalUnhandledErrorDetected(IUnknown *handler, CoreEventToken *token) override {
        if (!token) return E_POINTER;
        token->value = 0;
        if (!handler) return E_INVALIDARG;
        auto *entry = new (std::nothrow) Subscription{};
        if (!entry) return E_OUTOFMEMORY;
        HRESULT hr = RoGetAgileReference(AGILEREFERENCE_DEFAULT, __uuidof(IUnknown), handler, &entry->handler);
        if (FAILED(hr)) { delete entry; return hr; }
        AcquireSRWLockExclusive(&lock);
        // No reuse: prevent a stale token from unregistering a newer callback.
        if (next_token == INT64_MAX) {
            ReleaseSRWLockExclusive(&lock);
            entry->handler->Release(); delete entry;
            return E_OUTOFMEMORY;
        }
        entry->token = ++next_token;
        entry->next = subscriptions; subscriptions = entry;
        token->value = entry->token;
        ReleaseSRWLockExclusive(&lock);
        fprintf(stderr, "core_application_experiment: add_FinalUnhandledErrorDetected token=%lld\n", token->value);
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE remove_FinalUnhandledErrorDetected(CoreEventToken token) override {
        AcquireSRWLockExclusive(&lock);
        auto **cursor = &subscriptions;
        while (*cursor && (*cursor)->token != static_cast<UINT64>(token.value)) cursor = &(*cursor)->next;
        auto *entry = *cursor;
        if (entry) *cursor = entry->next;
        ReleaseSRWLockExclusive(&lock);
        // Release outside the lock: a callback's destructor may reenter us.
        if (entry) { entry->handler->Release(); delete entry; }
        fprintf(stderr, "core_application_experiment: remove_FinalUnhandledErrorDetected token=%lld\n", token.value);
        return S_OK; // Native remove also succeeds for unknown tokens.
    }
    HRESULT STDMETHODCALLTYPE GetDispatcherFromHwnd(UINT32, IInspectable **v) override { return unsupported("GetDispatcherFromHwnd", v); }
    HRESULT STDMETHODCALLTYPE IsMainWindowCreated(boolean *v) override { return unsupported("IsMainWindowCreated", v); }
    HRESULT STDMETHODCALLTYPE GetComponentSiteFromSiteId(GUID, IInspectable **v) override { return unsupported("GetComponentSiteFromSiteId", v); }
    HRESULT STDMETHODCALLTYPE GetCurrentView(INT32, IInspectable **v) override { return unsupported("GetCurrentView", v); }
    HRESULT STDMETHODCALLTYPE GetHwndFromViewOnASTA(IInspectable *, UINT32 *v) override { return unsupported("GetHwndFromViewOnASTA", v); }
    HRESULT STDMETHODCALLTYPE GetComponentSiteByViewInstanceId(UINT32, IInspectable **v) override { return unsupported("GetComponentSiteByViewInstanceId", v); }
};
static Factory factory;

extern "C" __declspec(dllexport) HRESULT WINAPI DllGetActivationFactory(HSTRING name, IActivationFactory **out) {
    if (!out) return E_POINTER;
    *out = nullptr;
    if (wcscmp(WindowsGetStringRawBuffer(name, nullptr), class_name)) return CLASS_E_CLASSNOTAVAILABLE;
    *out = &factory; factory.AddRef();
    return S_OK;
}
extern "C" __declspec(dllexport) HRESULT WINAPI DllCanUnloadNow() { return S_FALSE; }
