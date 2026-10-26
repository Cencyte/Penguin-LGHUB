// Inspect a newly prepared prefix. Does not start G HUB or access peripherals.
#include <windows.h>
#include <roapi.h>
#include <winstring.h>
#include <cstdio>
#include <cwchar>

static bool registry_string(HKEY root, const wchar_t *path, const wchar_t *name, const wchar_t *expected)
{
    wchar_t value[2048];
    DWORD bytes = sizeof(value);
    LSTATUS result = RegGetValueW(root, path, name, RRF_RT_REG_SZ, nullptr, value, &bytes);
    bool matches = result == ERROR_SUCCESS && !wcscmp(value, expected);
    printf("REGISTRY %ls %ls: %s\n", path, name ? name : L"(default)", matches ? "PASS" : "FAIL");
    return matches;
}

int main()
{
    int failures = 0;
    HRESULT hr = RoInitialize(RO_INIT_MULTITHREADED);
    if (FAILED(hr)) return 2;
    const wchar_t *classes[] = {
        L"Windows.Foundation.Metadata.ApiInformation",
        L"Windows.ApplicationModel.Core.CoreApplication",
        L"Windows.System.DispatcherQueue",
        L"Windows.System.Threading.ThreadPoolTimer",
        L"Windows.Storage.Streams.InMemoryRandomAccessStream",
        L"Windows.Storage.Streams.DataWriter",
        L"Windows.UI.Internal.Input.InputSite",
        L"Windows.UI.Internal.Input.ActivationConfigurationInputObject",
    };
    for (const auto name : classes)
    {
        HSTRING string = nullptr;
        IActivationFactory *factory = nullptr;
        hr = WindowsCreateString(name, static_cast<UINT32>(wcslen(name)), &string);
        if (SUCCEEDED(hr)) hr = RoGetActivationFactory(string, __uuidof(IActivationFactory),
                                                       reinterpret_cast<void **>(&factory));
        printf("FACTORY %ls hr=%08lx\n", name, static_cast<unsigned long>(hr));
        if (FAILED(hr) || !factory) ++failures;
        if (factory) factory->Release();
        WindowsDeleteString(string);
    }
    SC_HANDLE manager = OpenSCManagerW(nullptr, nullptr, SC_MANAGER_CONNECT);
    SC_HANDLE service = manager ? OpenServiceW(manager, L"LGHUBUpdaterService", SERVICE_QUERY_CONFIG | SERVICE_QUERY_STATUS) : nullptr;
    if (!service) ++failures;
    else
    {
        alignas(QUERY_SERVICE_CONFIGW) BYTE buffer[8192];
        DWORD needed = 0;
        SERVICE_STATUS status{};
        auto config = reinterpret_cast<QUERY_SERVICE_CONFIGW *>(buffer);
        const wchar_t *command = L"\"C:\\Program Files\\LGHUB\\lghub_updater.exe\" --run-as-service";
        if (!QueryServiceConfigW(service, config, sizeof(buffer), &needed) ||
            config->dwStartType != SERVICE_DEMAND_START || config->dwServiceType != SERVICE_WIN32_OWN_PROCESS ||
            wcscmp(config->lpBinaryPathName, command) ||
            !QueryServiceStatus(service, &status) || status.dwCurrentState != SERVICE_STOPPED)
            ++failures;
        else puts("SERVICE LGHUBUpdaterService: registered, demand start, stopped, command verified");
        CloseServiceHandle(service);
    }
    if (manager) CloseServiceHandle(manager);
    if (!registry_string(HKEY_LOCAL_MACHINE, L"Software\\Classes\\lghubapp\\shell\\open\\command", nullptr,
                         L"\"C:\\Program Files\\LGHUB\\lghub.exe\" --disable-gpu \"%1\"")) ++failures;
    if (!registry_string(HKEY_CURRENT_USER, L"Software\\Wine\\AppDefaults\\lghub_system_tray.exe", L"Version", L"win11")) ++failures;
    if (!registry_string(HKEY_CURRENT_USER, L"Software\\Wine\\AppDefaults\\lghub_agent.exe\\DllOverrides", L"mfsensorgroup", L"native")) ++failures;
    if (!registry_string(HKEY_CURRENT_USER, L"Software\\Wine\\DllOverrides", L"lghub_software_manager.exe", L"")) ++failures;
    RoUninitialize();
    printf("RESULT failures=%d\n", failures);
    return failures ? 1 : 0;
}
