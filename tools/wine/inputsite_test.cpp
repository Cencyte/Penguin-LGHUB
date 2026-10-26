/* InputSite's private RS5 GetForVisual ABI and real kernel-sink lifecycle.
 * Run in a copied prefix with the matched composition build on private Xvfb.
 */
#define INITGUID
#include <windows.h>
#include <winternl.h>
#include <roapi.h>
#include <winstring.h>
#include <cstdio>
#include <cstring>

static const GUID statics_id = {0x8ca0442d,0xae80,0x47b0,{0xaf,0x23,0x81,0x3f,0xc1,0xda,0x9c,0xd0}};
static const GUID site_id = {0xcfb32fa7,0x5399,0x5f3d,{0x82,0xba,0x96,0x41,0xf1,0x06,0xff,0xcd}};
static const GUID site_rs5_id = {0xf77d91d0,0x46ca,0x4464,{0x99,0xc1,0x89,0x02,0xae,0xd1,0x76,0x80}};
static const GUID partner_id = {0x53456138,0x20b5,0x4f17,{0x8f,0x6c,0xf3,0x79,0xa1,0xac,0x98,0xd0}};
static const GUID internal_id = {0x92179cc0,0xae1f,0x48bc,{0xa3,0x54,0x8f,0x7a,0x88,0x00,0xf2,0xfb}};
static const GUID closable_id = {0x30d5a829,0x7fa4,0x4026,{0x83,0xbb,0xd7,0x5b,0xae,0x4e,0xa9,0x9e}};
struct Statics : IInspectable
{
    virtual HRESULT STDMETHODCALLTYPE GetForVisual(IInspectable *, IInspectable **) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetForHwnd(UINT64, IInspectable **) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetForCoreWindow(IInspectable *, IInspectable **) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetForCoreComponentInputSource(IInspectable *, IInspectable **) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetForCoreIndependentInputSource(IInspectable *, IInspectable **) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetForCurrentProcess(IInspectable **) = 0;
};
struct Closable : IInspectable { virtual HRESULT STDMETHODCALLTYPE Close() = 0; };
static LONG checks, failures;
#define CHECK(x) do { InterlockedIncrement(&checks); if (!(x)) { InterlockedIncrement(&failures); \
    fprintf(stderr, "FAIL line %d: %s error=%lu\n", __LINE__, #x, GetLastError()); } } while (0)

/* Deliberately independent vtable, not shared implementation headers. All
 * slots except QI/refcount/20 reject calls, detecting a wrong method offset. */
struct Visual
{
    void **vtable;
    LONG refs = 1;
    HANDLE sink;
    HRESULT result = S_OK;
    bool expose = true;
    bool modern = false;
    IInspectable *iface() { return reinterpret_cast<IInspectable *>(this); }
};
static ULONG WINAPI visual_add(Visual *v) { return InterlockedIncrement(&v->refs); }
static ULONG WINAPI visual_release(Visual *v) { return InterlockedDecrement(&v->refs); }
static HRESULT WINAPI visual_qi(Visual *v, REFIID iid, void **out)
{
    *out = nullptr;
    if (iid == IID_IUnknown || iid == IID_IInspectable ||
        (iid == (v->modern ? internal_id : partner_id) && v->expose))
    { *out = v; visual_add(v); return S_OK; }
    return E_NOINTERFACE;
}
static HRESULT WINAPI visual_unused() { CHECK(false); return E_NOTIMPL; }
static HRESULT WINAPI visual_sink(Visual *v, INT64 *out)
{ *out = reinterpret_cast<INT_PTR>(v->sink); return v->result; }
static void *visual_vtable[21];
static void *modern_vtable[12];

struct Route { UINT type, reserved; UINT64 window, data[5]; };
struct Descriptor { UINT size, view; Route routes[5]; UINT flags, reserved; };
using CreateSink = NTSTATUS (WINAPI *)(const void *, HANDLE *);
using QueryObject = NTSTATUS (WINAPI *)(HANDLE, OBJECT_INFORMATION_CLASS, void *, ULONG, ULONG *);
static QueryObject query_object;
static ULONG handles(HANDLE sink)
{
    ULONG info[16] = {};
    CHECK(query_object(sink, ObjectBasicInformation, info, sizeof(info), nullptr) == 0);
    CHECK(info[2] > 0); // Real per-object count; GetProcessHandleCount is a Wine stub.
    return info[2];
}
struct ThreadData { Statics *factory; Visual *visual; IInspectable *anchor; };
static DWORD WINAPI worker(void *arg)
{
    auto *data = static_cast<ThreadData *>(arg);
    for (unsigned i = 0; i < 128; ++i)
    {
        IInspectable *site = nullptr;
        CHECK(data->factory->GetForVisual(data->visual->iface(), &site) == S_OK);
        CHECK(site && (!data->anchor || site == data->anchor));
        if (site) site->Release();
    }
    return 0;
}

int main(int argc, char **argv)
{
    CHECK(SUCCEEDED(RoInitialize(RO_INIT_MULTITHREADED)));
    HMODULE module = LoadLibraryW(L"windows.ui.dll");
    auto get = reinterpret_cast<HRESULT (WINAPI *)(HSTRING, IActivationFactory **)>(GetProcAddress(module, "DllGetActivationFactory"));
    CHECK(get);
    if (!get) return 1;
    HSTRING name = nullptr;
    CHECK(WindowsCreateString(L"Windows.UI.Internal.Input.InputSite", 35, &name) == S_OK);
    IActivationFactory *activation = nullptr;
    HRESULT hr = get(name, &activation);
    if (argc > 1 && !strcmp(argv[1], "--disabled"))
    {
        CHECK(hr == CLASS_E_CLASSNOTAVAILABLE && !activation);
        WindowsDeleteString(name);
        RoUninitialize();
        printf("InputSite disabled: %ld checks, %ld failures\n", checks, failures);
        return !!failures;
    }
    CHECK(hr == S_OK && activation);
    if (!activation) return 1;
    Statics *factory = nullptr, *registered = nullptr;
    CHECK(activation->QueryInterface(statics_id, reinterpret_cast<void **>(&factory)) == S_OK);
    CHECK(RoGetActivationFactory(name, statics_id, reinterpret_cast<void **>(&registered)) == S_OK);
    CHECK(factory == registered);
    if (registered) registered->Release();
    if (!factory) return 1;
    IUnknown *a = nullptr, *b = nullptr;
    CHECK(activation->QueryInterface(IID_IUnknown, reinterpret_cast<void **>(&a)) == S_OK);
    CHECK(factory->QueryInterface(IID_IUnknown, reinterpret_cast<void **>(&b)) == S_OK && a == b);
    if (a) a->Release();
    if (b) b->Release();
    IInspectable *site = reinterpret_cast<IInspectable *>(1);
    CHECK(activation->ActivateInstance(&site) == E_NOTIMPL && !site);
    activation->Release();
    CHECK(factory->GetForVisual(nullptr, &site) == E_INVALIDARG && !site);
    CHECK(factory->GetForVisual(nullptr, nullptr) == E_POINTER);
    CHECK(factory->GetForHwnd(0, &site) == E_NOTIMPL && !site);
    CHECK(factory->GetForCoreWindow(nullptr, &site) == E_NOTIMPL && !site);
    CHECK(factory->GetForCoreComponentInputSource(nullptr, &site) == E_NOTIMPL && !site);
    CHECK(factory->GetForCoreIndependentInputSource(nullptr, &site) == E_NOTIMPL && !site);
    CHECK(factory->GetForCurrentProcess(&site) == E_NOTIMPL && !site);

    auto create = reinterpret_cast<CreateSink>(GetProcAddress(LoadLibraryW(L"win32u.dll"), "NtCreateCompositionInputSink"));
    query_object = reinterpret_cast<QueryObject>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "NtQueryObject"));
    CHECK(create && query_object);
    if (!create || !query_object) return 1;
    HWND window = CreateWindowExW(0, L"STATIC", L"private inputsite test", 0, 0, 0, 0, 0, HWND_MESSAGE, nullptr, nullptr, nullptr);
    CHECK(window);
    Descriptor desc = {}; desc.size = sizeof(desc); desc.view = 0x1234;
    for (unsigned i = 0; i < 4; ++i) { desc.routes[i].type = 2; desc.routes[i].window = reinterpret_cast<UINT_PTR>(window); }
    HANDLE sink = nullptr, second = nullptr, duplicate = nullptr, restricted = nullptr;
    CHECK(create(&desc, &sink) == 0);
    CHECK(create(&desc, &second) == 0);
    if (!sink || sink == INVALID_HANDLE_VALUE) return 1;
    CHECK(DuplicateHandle(GetCurrentProcess(), sink, GetCurrentProcess(), &duplicate, 0, FALSE, DUPLICATE_SAME_ACCESS));
    CHECK(DuplicateHandle(GetCurrentProcess(), sink, GetCurrentProcess(), &restricted, 0, FALSE, 0));
    for (auto &entry : visual_vtable) entry = reinterpret_cast<void *>(visual_unused);
    visual_vtable[0] = reinterpret_cast<void *>(visual_qi);
    visual_vtable[1] = reinterpret_cast<void *>(visual_add);
    visual_vtable[2] = reinterpret_cast<void *>(visual_release);
    visual_vtable[20] = reinterpret_cast<void *>(visual_sink);
    memcpy(modern_vtable, visual_vtable, sizeof(modern_vtable));
    modern_vtable[9] = reinterpret_cast<void *>(visual_sink);
    Visual visual{visual_vtable, 1, sink}, same{visual_vtable, 1, duplicate}, other{visual_vtable, 1, second};
    ULONG before = handles(sink);
    visual.expose = false;
    CHECK(factory->GetForVisual(visual.iface(), &site) == E_NOINTERFACE && !site);
    visual.expose = true; visual.result = E_ACCESSDENIED;
    CHECK(factory->GetForVisual(visual.iface(), &site) == E_ACCESSDENIED && !site);
    visual.result = S_OK; visual.sink = restricted;
    CHECK(factory->GetForVisual(visual.iface(), &site) == E_ACCESSDENIED && !site);
    visual.sink = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    CHECK(FAILED(factory->GetForVisual(visual.iface(), &site)) && !site);
    CloseHandle(visual.sink); visual.sink = nullptr;
    CHECK(FAILED(factory->GetForVisual(visual.iface(), &site)) && !site);
    visual.sink = sink;
    CHECK(handles(sink) == before);
    CHECK(factory->GetForVisual(visual.iface(), &site) == S_OK && site);
    if (!site) return 1;
    CHECK(handles(sink) == before + 1);
    IInspectable *again = nullptr, *different = nullptr;
    CHECK(factory->GetForVisual(same.iface(), &again) == S_OK && again == site);
    CHECK(factory->GetForVisual(other.iface(), &different) == S_OK && different != site);
    if (again) again->Release();
    if (different) different->Release();
    Visual modern{modern_vtable, 1, duplicate}; modern.modern = true;
    CHECK(factory->GetForVisual(modern.iface(), &again) == S_OK && again == site);
    if (again) again->Release();
    if (argc > 1 && !strcmp(argv[1], "--framework"))
    {
        HMODULE framework = LoadLibraryExW(L"C:\\Program Files\\LGHUB\\system_tray\\Microsoft.Internal.FrameworkUdk.dll",
                                          nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
        CHECK(framework);
        auto get_site = reinterpret_cast<HRESULT (WINAPI *)(HANDLE, IUnknown **)>(
            framework ? GetProcAddress(framework, "InputPrivates_InputSite_GetForInputSink") : nullptr);
        CHECK(get_site);
        if (get_site)
        {
            IUnknown *native_adapter_site = nullptr;
            hr = get_site(duplicate, &native_adapter_site);
            CHECK(hr == S_OK && native_adapter_site == static_cast<IUnknown *>(site));
            if (native_adapter_site) native_adapter_site->Release();
            printf("FrameworkUdk native adapter: hr=%08lx\n", static_cast<unsigned long>(hr));
        }
    }
    CHECK(site->QueryInterface(site_id, reinterpret_cast<void **>(&again)) == S_OK && again == site);
    if (again) again->Release();
    CHECK(site->QueryInterface(site_rs5_id, reinterpret_cast<void **>(&again)) == S_OK && again == site);
    if (again) again->Release();
    void *unsupported = reinterpret_cast<void *>(1);
    CHECK(site->QueryInterface(IID_IStream, &unsupported) == E_NOINTERFACE && !unsupported);
    ULONG count = 0; IID *ids = nullptr;
    CHECK(site->GetIids(&count, &ids) == S_OK && count == 3 && ids);
    if (ids) { CHECK(ids[0] == site_rs5_id && ids[1] == site_id && ids[2] == closable_id); CoTaskMemFree(ids); }
    HSTRING class_name = nullptr; TrustLevel trust = FullTrust;
    CHECK(site->GetRuntimeClassName(&class_name) == S_OK);
    CHECK(!wcscmp(WindowsGetStringRawBuffer(class_name, nullptr), L"Windows.UI.Internal.Input.InputSite"));
    WindowsDeleteString(class_name);
    CHECK(site->GetTrustLevel(&trust) == S_OK && trust == BaseTrust);
    CHECK(site->QueryInterface(IID_IUnknown, nullptr) == E_POINTER);
    CHECK(site->GetIids(nullptr, &ids) == E_POINTER);
    CHECK(site->GetRuntimeClassName(nullptr) == E_POINTER);
    CHECK(site->GetTrustLevel(nullptr) == E_POINTER);
    Closable *closable = nullptr;
    CHECK(site->QueryInterface(closable_id, reinterpret_cast<void **>(&closable)) == S_OK);
    if (!closable) return 1;
    CHECK(closable->QueryInterface(IID_IUnknown, reinterpret_cast<void **>(&a)) == S_OK);
    CHECK(site->QueryInterface(IID_IUnknown, reinterpret_cast<void **>(&b)) == S_OK && a == b);
    a->Release(); b->Release();
    ThreadData data{factory, &same, site}; HANDLE threads[8];
    for (auto &thread : threads) { thread = CreateThread(nullptr, 0, worker, &data, 0, nullptr); CHECK(thread); }
    CHECK(WaitForMultipleObjects(8, threads, TRUE, 30000) == WAIT_OBJECT_0);
    for (auto thread : threads) CloseHandle(thread);
    CHECK(handles(sink) == before + 1);
    CHECK(closable->Close() == S_OK);
    CHECK(closable->Close() == S_OK);
    CHECK(handles(sink) == before);
    CHECK(factory->GetForVisual(same.iface(), &again) == S_OK && again != site);
    site->Release(); closable->Release(); site = again;
    CHECK(CloseHandle(sink)); visual.sink = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    CHECK(FAILED(factory->GetForVisual(visual.iface(), &again)) && !again);
    CHECK(factory->GetForVisual(same.iface(), &again) == S_OK && again == site);
    if (again) again->Release();
    CHECK(handles(duplicate) == before); // Original handle gone, retained site handle still live.
    site->Release();
    CHECK(handles(duplicate) == before - 1);
    data.anchor = nullptr; // Exercise lookup racing the last release/cache removal.
    for (auto &thread : threads) { thread = CreateThread(nullptr, 0, worker, &data, 0, nullptr); CHECK(thread); }
    CHECK(WaitForMultipleObjects(8, threads, TRUE, 30000) == WAIT_OBJECT_0);
    for (auto thread : threads) CloseHandle(thread);
    CHECK(handles(duplicate) == before - 1);
    CHECK(visual.refs == 1 && same.refs == 1 && other.refs == 1 && modern.refs == 1);
    CloseHandle(visual.sink); CloseHandle(duplicate); CloseHandle(restricted); CloseHandle(second);
    DestroyWindow(window); factory->Release(); WindowsDeleteString(name); RoUninitialize();
    printf("InputSite: %ld checks, %ld failures\n", checks, failures);
    return !!failures;
}
