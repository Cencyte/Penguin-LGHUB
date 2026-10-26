/* Native FrameworkUdk adapter and independent ActivationConfiguration ABI.
 * Uses real composition input sinks on private Xvfb in a stopped lab prefix. */
#define INITGUID
#include <windows.h>
#include <winternl.h>
#include <roapi.h>
#include <winstring.h>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#ifndef RO_E_CLOSED
#define RO_E_CLOSED ((HRESULT)0x80000013)
#endif

static const wchar_t class_name[] = L"Windows.UI.Internal.Input.ActivationConfigurationInputObject";
static const GUID statics_id = {0x4c8421ca,0x3863,0x5236,{0xb0,0x9a,0x75,0x7c,0xf6,0x8c,0x5f,0xa5}};
static const GUID config_id = {0x773ebb88,0x99de,0x4a39,{0xb9,0xfa,0x02,0xad,0xae,0xc2,0x5d,0x4d}};
static const GUID closable_id = {0x30d5a829,0x7fa4,0x4026,{0x83,0xbb,0xd7,0x5b,0xae,0x4e,0xa9,0x9e}};
struct Config : IInspectable
{
    virtual HRESULT STDMETHODCALLTYPE get_ActivatePolicy(INT32 *) = 0;
    virtual HRESULT STDMETHODCALLTYPE put_ActivatePolicy(INT32) = 0;
    virtual HRESULT STDMETHODCALLTYPE get_EatPolicy(INT32 *) = 0;
    virtual HRESULT STDMETHODCALLTYPE put_EatPolicy(INT32) = 0;
};
struct Statics : IInspectable { virtual HRESULT STDMETHODCALLTYPE GetForInputSite(IInspectable *, Config **) = 0; };
struct Closable : IInspectable { virtual HRESULT STDMETHODCALLTYPE Close() = 0; };
struct Lifted : IUnknown
{
    // FrameworkUdk RVA 0x94e18: unlike the WinRT interface, setters come first.
    virtual HRESULT STDMETHODCALLTYPE put_ActivatePolicy(INT32) = 0;
    virtual HRESULT STDMETHODCALLTYPE get_ActivatePolicy(INT32 *) = 0;
    virtual HRESULT STDMETHODCALLTYPE put_EatPolicy(INT32) = 0;
    virtual HRESULT STDMETHODCALLTYPE get_EatPolicy(INT32 *) = 0;
};
static LONG checks, failures;
#define CHECK(x) do { InterlockedIncrement(&checks); if (!(x)) { InterlockedIncrement(&failures); \
    fprintf(stderr, "FAIL line %d: %s error=%lu\n", __LINE__, #x, GetLastError()); } } while (0)
struct Route { UINT type, reserved; UINT64 window, data[5]; };
struct Descriptor { UINT size, view; Route routes[5]; UINT flags, reserved; };
using QueryObject = NTSTATUS (WINAPI *)(HANDLE, OBJECT_INFORMATION_CLASS, void *, ULONG, ULONG *);
static QueryObject query_object;
static ULONG handles(HANDLE sink)
{
    ULONG info[16] = {};
    CHECK(query_object(sink, ObjectBasicInformation, info, sizeof(info), nullptr) == 0);
    return info[2];
}
struct ThreadData { Statics *factory; IInspectable *site; Config *anchor; };
static DWORD WINAPI worker(void *arg)
{
    auto *d = static_cast<ThreadData *>(arg);
    for (unsigned i = 0; i < 128; ++i)
    {
        Config *c = nullptr;
        CHECK(d->factory->GetForInputSite(d->site, &c) == S_OK);
        CHECK(c && (!d->anchor || c == d->anchor));
        if (c)
        {
            INT32 value = 0;
            CHECK(c->put_ActivatePolicy(17) == S_OK);
            CHECK(c->get_ActivatePolicy(&value) == S_OK && value == 17);
            CHECK(c->put_EatPolicy(-19) == S_OK);
            CHECK(c->get_EatPolicy(&value) == S_OK && value == -19);
            c->Release();
        }
    }
    return 0;
}
static void threaded(ThreadData *data)
{
    HANDLE threads[8];
    for (auto &thread : threads) { thread = CreateThread(nullptr, 0, worker, data, 0, nullptr); CHECK(thread); }
    CHECK(WaitForMultipleObjects(8, threads, TRUE, 30000) == WAIT_OBJECT_0);
    for (auto thread : threads) CloseHandle(thread);
}
int main(int argc, char **argv)
{
    CHECK(SUCCEEDED(RoInitialize(RO_INIT_MULTITHREADED)));
    auto get = reinterpret_cast<HRESULT (WINAPI *)(HSTRING, IActivationFactory **)>(
        GetProcAddress(LoadLibraryW(L"windows.ui.dll"), "DllGetActivationFactory"));
    CHECK(get); if (!get) return 1;
    HSTRING name = nullptr;
    CHECK(WindowsCreateString(class_name, ARRAYSIZE(class_name)-1, &name) == S_OK);
    IActivationFactory *activation = nullptr;
    HRESULT hr = get(name, &activation);
    if (argc > 1 && !strcmp(argv[1], "--disabled"))
    {
        CHECK(hr == CLASS_E_CLASSNOTAVAILABLE && !activation);
        WindowsDeleteString(name); RoUninitialize();
        printf("ActivationConfiguration disabled: %ld checks, %ld failures\n", checks, failures);
        return !!failures;
    }
    CHECK(hr == S_OK && activation); if (!activation) return 1;
    Statics *factory = nullptr, *registered = nullptr;
    CHECK(activation->QueryInterface(statics_id, reinterpret_cast<void **>(&factory)) == S_OK);
    CHECK(RoGetActivationFactory(name, statics_id, reinterpret_cast<void **>(&registered)) == S_OK);
    CHECK(factory && factory == registered); if (!factory) return 1;
    registered->Release();
    Config *config = reinterpret_cast<Config *>(1);
    CHECK(activation->ActivateInstance(reinterpret_cast<IInspectable **>(&config)) == E_NOTIMPL && !config);
    CHECK(factory->GetForInputSite(nullptr, &config) == E_INVALIDARG && !config);
    CHECK(factory->GetForInputSite(nullptr, nullptr) == E_POINTER);
    CHECK(factory->GetForInputSite(activation, &config) == E_NOINTERFACE && !config);
    IUnknown *a = nullptr, *b = nullptr;
    CHECK(factory->QueryInterface(IID_IUnknown, reinterpret_cast<void **>(&a)) == S_OK);
    CHECK(activation->QueryInterface(IID_IUnknown, reinterpret_cast<void **>(&b)) == S_OK && a == b);
    a->Release(); b->Release(); activation->Release();

    HMODULE framework = LoadLibraryExW(L"C:\\Program Files\\LGHUB\\system_tray\\Microsoft.Internal.FrameworkUdk.dll",
                                      nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    auto get_site = reinterpret_cast<HRESULT (WINAPI *)(HANDLE, IInspectable **)>(
        framework ? GetProcAddress(framework, "InputPrivates_InputSite_GetForInputSink") : nullptr);
    auto get_config = reinterpret_cast<HRESULT (WINAPI *)(IUnknown *, Lifted **)>(
        framework ? GetProcAddress(framework, "InputPrivates_GetActivationConfiguration") : nullptr);
    auto create = reinterpret_cast<NTSTATUS (WINAPI *)(const void *, HANDLE *)>(
        GetProcAddress(LoadLibraryW(L"win32u.dll"), "NtCreateCompositionInputSink"));
    query_object = reinterpret_cast<QueryObject>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "NtQueryObject"));
    CHECK(get_site && get_config && create && query_object);
    if (!get_site || !get_config || !create || !query_object) return 1;
    Descriptor descriptor = {}; descriptor.size = sizeof(descriptor); descriptor.view = 29;
    HANDLE sinks[2] = {}; IInspectable *sites[2] = {};
    for (unsigned i=0; i<2; ++i)
    {
        CHECK(create(&descriptor, &sinks[i]) == 0 && sinks[i]);
        CHECK(get_site(sinks[i], &sites[i]) == S_OK && sites[i]);
        if (!sites[i]) return 1;
    }
    CHECK(factory->GetForInputSite(sites[0], &config) == S_OK && config);
    if (!config) return 1;
    Config *again = nullptr, *other = nullptr;
    CHECK(factory->GetForInputSite(sites[0], &again) == S_OK && again == config);
    again->Release();
    CHECK(factory->GetForInputSite(sites[1], &other) == S_OK && other != config);
    INT32 value = 99;
    CHECK(config->get_ActivatePolicy(&value) == S_OK && !value);
    CHECK(config->get_EatPolicy(&value) == S_OK && !value);
    CHECK(config->get_ActivatePolicy(nullptr) == E_POINTER);
    CHECK(config->get_EatPolicy(nullptr) == E_POINTER);
    for (INT32 policy : {0, 1, 2, -1, 0x7fffffff})
    {
        CHECK(config->put_ActivatePolicy(policy) == S_OK);
        CHECK(config->get_ActivatePolicy(&value) == S_OK && value == policy);
        CHECK(config->get_EatPolicy(&value) == S_OK && !value);
        CHECK(config->put_EatPolicy(policy) == S_OK);
        CHECK(config->get_EatPolicy(&value) == S_OK && value == policy);
        CHECK(config->put_EatPolicy(0) == S_OK);
        CHECK(other->get_ActivatePolicy(&value) == S_OK && !value);
        CHECK(other->get_EatPolicy(&value) == S_OK && !value);
    }
    CHECK(config->QueryInterface(config_id, reinterpret_cast<void **>(&again)) == S_OK && again == config);
    again->Release();
    void *unsupported = reinterpret_cast<void *>(1);
    CHECK(config->QueryInterface(IID_IStream, &unsupported) == E_NOINTERFACE && !unsupported);
    CHECK(config->QueryInterface(IID_IUnknown, nullptr) == E_POINTER);
    ULONG count = 0; IID *ids = nullptr;
    CHECK(config->GetIids(&count, &ids) == S_OK && count == 2 && ids);
    if (ids) { CHECK(ids[0] == config_id && ids[1] == closable_id); CoTaskMemFree(ids); }
    HSTRING class_string = nullptr; TrustLevel trust = FullTrust;
    CHECK(config->GetRuntimeClassName(&class_string) == S_OK);
    CHECK(!wcscmp(WindowsGetStringRawBuffer(class_string, nullptr), class_name));
    WindowsDeleteString(class_string);
    CHECK(config->GetTrustLevel(&trust) == S_OK && trust == BaseTrust);
    Closable *close = nullptr, *close_site = nullptr;
    CHECK(config->QueryInterface(closable_id, reinterpret_cast<void **>(&close)) == S_OK && close);
    CHECK(close->QueryInterface(IID_IUnknown, reinterpret_cast<void **>(&a)) == S_OK);
    CHECK(config->QueryInterface(IID_IUnknown, reinterpret_cast<void **>(&b)) == S_OK && a == b);
    a->Release(); b->Release();
    Lifted *lifted = nullptr;
    CHECK(get_config(sites[0], &lifted) == S_OK && lifted);
    if (!lifted) return 1;
    CHECK(config->put_ActivatePolicy(2) == S_OK);
    CHECK(lifted->get_ActivatePolicy(&value) == S_OK && value == 2);
    CHECK(lifted->put_ActivatePolicy(1) == S_OK);
    CHECK(config->get_ActivatePolicy(&value) == S_OK && value == 1);
    CHECK(config->put_EatPolicy(2) == S_OK);
    CHECK(lifted->get_EatPolicy(&value) == S_OK && value == 2);
    CHECK(lifted->put_EatPolicy(1) == S_OK);
    CHECK(config->get_EatPolicy(&value) == S_OK && value == 1);
    ThreadData data{factory, sites[0], config}; threaded(&data);
    CHECK(close->Close() == S_OK && close->Close() == S_OK);
    value = 123;
    CHECK(config->get_ActivatePolicy(&value) == RO_E_CLOSED && value == 123);
    CHECK(config->put_ActivatePolicy(0) == RO_E_CLOSED);
    CHECK(config->get_EatPolicy(&value) == RO_E_CLOSED && value == 123);
    CHECK(config->put_EatPolicy(0) == RO_E_CLOSED);
    CHECK(lifted->put_ActivatePolicy(0) == RO_E_CLOSED);
    CHECK(lifted->get_EatPolicy(&value) == RO_E_CLOSED);
    CHECK(factory->GetForInputSite(sites[0], &again) == S_OK && again != config);
    CHECK(again->get_ActivatePolicy(&value) == S_OK && !value);
    CHECK(again->get_EatPolicy(&value) == S_OK && !value);
    lifted->Release(); close->Release(); config->Release(); again->Release();
    data.anchor = nullptr; threaded(&data); // Lookup racing the final release.
    CHECK(sites[1]->QueryInterface(closable_id, reinterpret_cast<void **>(&close_site)) == S_OK);
    CHECK(close_site->Close() == S_OK);
    CHECK(other->put_ActivatePolicy(0) == RO_E_CLOSED);
    CHECK(factory->GetForInputSite(sites[1], &again) == RO_E_CLOSED && !again);
    close_site->Release(); other->Release(); sites[1]->Release();
    CHECK(handles(sinks[1]) == 1);
    // The config alone keeps the site and its kernel sink alive.
    CHECK(factory->GetForInputSite(sites[0], &config) == S_OK && config);
    sites[0]->Release();
    CHECK(handles(sinks[0]) == 2);
    CHECK(config->put_ActivatePolicy(1) == S_OK);
    CHECK(config->get_ActivatePolicy(&value) == S_OK && value == 1);
    config->Release();
    CHECK(handles(sinks[0]) == 1);
    for (auto sink : sinks) CloseHandle(sink);
    factory->Release(); WindowsDeleteString(name); RoUninitialize();
    printf("ActivationConfiguration + native FrameworkUdk: %ld checks, %ld failures\n", checks, failures);
    return !!failures;
}
