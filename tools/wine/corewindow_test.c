/* Headless regression for the Wine 10.20 CoreWindow desktop-query backport.
 * --register registers the DLL in the test Wine prefix only.
 * CoreWindow creation, dispatcher and UWP lifecycle remain unimplemented.
 * Use SDK C interfaces to avoid this MinGW version's conflicting C++
 * IReference<BYTE>/IReference<boolean> specializations.
 */
#define COBJMACROS
#define INITGUID
#include <windows.h>
#include <roapi.h>
#include <winstring.h>
#include <windows.ui.core.h>
#include <windows.ui.viewmanagement.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

typedef __x_ABI_CWindows_CUI_CCore_CICoreWindow ICoreWindow;
typedef __x_ABI_CWindows_CUI_CCore_CICoreWindowStatic ICoreWindowStatic;
typedef __x_ABI_CWindows_CUI_CViewManagement_CIUISettings IUISettings;
typedef HRESULT (WINAPI *GetFactory)(HSTRING, IActivationFactory **);
static LONG failures;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); InterlockedIncrement(&failures); } } while (0)
#define RELEASE(p) do { if (p) { (p)->lpVtbl->Release(p); (p) = NULL; } } while (0)

static HSTRING string(const wchar_t *text)
{
    HSTRING value = NULL;
    CHECK(WindowsCreateString(text, (UINT32)wcslen(text), &value) == S_OK);
    return value;
}

static void no_window(ICoreWindowStatic *statics)
{
    ICoreWindow *window = (ICoreWindow *)1;
    HRESULT hr = statics->lpVtbl->GetForCurrentThread(statics, &window);
    CHECK(hr == S_OK && !window);
    if (SUCCEEDED(hr)) RELEASE(window);
}

static DWORD WINAPI worker(void *argument)
{
    ICoreWindowStatic *shared = argument, *local = NULL;
    HRESULT initialized = RoInitialize(RO_INIT_MULTITHREADED);
    CHECK(SUCCEEDED(initialized));
    if (SUCCEEDED(initialized))
    {
        HSTRING name = string(L"Windows.UI.Core.CoreWindow");
        HRESULT hr = RoGetActivationFactory(name, &IID___x_ABI_CWindows_CUI_CCore_CICoreWindowStatic, (void **)&local);
        CHECK(hr == S_OK && local);
        for (unsigned i = 0; i < 16; ++i)
        {
            no_window(shared); /* Agile factory, queried from another apartment. */
            if (local) no_window(local);
        }
        RELEASE(local);
        WindowsDeleteString(name);
    }
    RELEASE(shared);
    if (SUCCEEDED(initialized)) RoUninitialize();
    return 0;
}

static void exercise(GetFactory get)
{
    HSTRING name = string(L"Windows.UI.Core.CoreWindow");
    IActivationFactory *factory = NULL;
    ICoreWindowStatic *statics = NULL, *activated = NULL;
    IUnknown *a = NULL, *b = NULL;
    IInspectable *inspectable = NULL;
    IAgileObject *agile = NULL;
    void *unknown = (void *)1;
    HANDLE thread;
    HWND hwnd;
    HRESULT hr = get(name, &factory);
    CHECK(hr == S_OK && factory);
    if (FAILED(hr) || !factory) goto done;
    CHECK(IActivationFactory_QueryInterface(factory, &IID___x_ABI_CWindows_CUI_CCore_CICoreWindowStatic, (void **)&statics) == S_OK);
    if (!statics) goto done;
    CHECK(IActivationFactory_QueryInterface(factory, &IID_IUnknown, (void **)&a) == S_OK);
    CHECK(statics->lpVtbl->QueryInterface(statics, &IID_IUnknown, (void **)&b) == S_OK);
    CHECK(a == b);
    CHECK(statics->lpVtbl->QueryInterface(statics, &IID_IInspectable, (void **)&inspectable) == S_OK);
    CHECK(IActivationFactory_QueryInterface(factory, &IID_IAgileObject, (void **)&agile) == S_OK);
    CHECK(statics->lpVtbl->QueryInterface(statics, &IID_IStream, &unknown) == E_NOINTERFACE && !unknown);
    RELEASE(a); RELEASE(b); RELEASE(inspectable); RELEASE(agile); RELEASE(factory);
    no_window(statics); /* Statics lifetime survives the caller's factory reference. */
    hwnd = CreateWindowExW(0, L"STATIC", L"Hidden CoreWindow query regression", WS_OVERLAPPED,
                          0, 0, 32, 32, NULL, NULL, GetModuleHandleW(NULL), NULL);
    CHECK(hwnd);
    no_window(statics); /* An ordinary HWND does not create a UWP CoreWindow. */
    if (hwnd) CHECK(DestroyWindow(hwnd));
    hr = RoGetActivationFactory(name, &IID___x_ABI_CWindows_CUI_CCore_CICoreWindowStatic, (void **)&activated);
    CHECK(hr == S_OK && activated);
    if (activated) no_window(activated);
    statics->lpVtbl->AddRef(statics);
    thread = CreateThread(NULL, 0, worker, statics, 0, NULL);
    CHECK(thread);
    if (thread)
    {
        for (unsigned i = 0; i < 16; ++i) no_window(statics);
        CHECK(WaitForSingleObject(thread, INFINITE) == WAIT_OBJECT_0);
        CloseHandle(thread);
    }
    else statics->lpVtbl->Release(statics);
done:
    RELEASE(activated); RELEASE(statics); RELEASE(factory);
    WindowsDeleteString(name);
}

static void existing_factories(GetFactory get)
{
    const wchar_t *names[] = {L"Windows.UI.ViewManagement.AccessibilitySettings", L"Windows.UI.ViewManagement.UISettings",
                             L"Windows.UI.ViewManagement.UIViewSettings", L"Windows.UI.ViewManagement.InputPane"};
    HSTRING name;
    IActivationFactory *missing = (IActivationFactory *)1;
    for (unsigned i = 0; i < sizeof(names) / sizeof(names[0]); ++i)
    {
        IActivationFactory *factory = NULL;
        IInspectable *object = NULL;
        IUISettings *settings = NULL;
        name = string(names[i]);
        CHECK(get(name, &factory) == S_OK && factory);
        if (factory && !wcscmp(names[i], L"Windows.UI.ViewManagement.UISettings"))
        {
            CHECK(IActivationFactory_ActivateInstance(factory, &object) == S_OK && object);
            if (object)
                CHECK(IInspectable_QueryInterface(object, &IID___x_ABI_CWindows_CUI_CViewManagement_CIUISettings, (void **)&settings) == S_OK);
        }
        RELEASE(settings); RELEASE(object); RELEASE(factory);
        WindowsDeleteString(name);
    }
    name = string(L"Windows.UI.Core.MissingGHubRegressionClass");
    CHECK(get(name, &missing) == CLASS_E_CLASSNOTAVAILABLE && !missing);
    WindowsDeleteString(name);
}

int main(int argc, char **argv)
{
    BOOL baseline = argc == 2 && !strcmp(argv[1], "--expect-missing");
    BOOL registration = argc == 2 && !strcmp(argv[1], "--register");
    HMODULE module;
    GetFactory get;
    HRESULT hr;
    if (argc > 1 && !baseline && !registration) return 2;
    setbuf(stderr, NULL);
    hr = RoInitialize(RO_INIT_SINGLETHREADED);
    CHECK(SUCCEEDED(hr));
    if (FAILED(hr)) return 1;
    module = LoadLibraryW(L"windows.ui.dll");
    CHECK(module);
    if (!module) return 1;
    get = (GetFactory)GetProcAddress(module, "DllGetActivationFactory");
    CHECK(get);
    if (!get) return 1;
    if (registration)
    {
        HRESULT (WINAPI *reg)(void) = (void *)GetProcAddress(module, "DllRegisterServer");
        CHECK(reg);
        if (!reg || FAILED(reg())) return 1;
    }
    existing_factories(get);
    if (baseline)
    {
        HSTRING name = string(L"Windows.UI.Core.CoreWindow");
        IActivationFactory *factory = (IActivationFactory *)1;
        hr = get(name, &factory);
        fprintf(stderr, "Baseline CoreWindow factory: %08lx pointer=%p\n", (unsigned long)hr, (void *)factory);
        CHECK(hr == CLASS_E_CLASSNOTAVAILABLE && !factory);
        WindowsDeleteString(name);
    }
    else exercise(get);
    FreeLibrary(module);
    RoUninitialize();
    fprintf(stderr, "CoreWindow %s: %s (%ld failures)\n", baseline ? "baseline" : "desktop query", failures ? "FAIL" : "PASS", failures);
    return !!failures;
}
