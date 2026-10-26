#include <windows.h>
#include <roapi.h>
#include <winstring.h>
#include <stdio.h>
#include <stdlib.h>
#include <initializer_list>
#include "api_information_abi.h"

#define CHECK(expr) do { if (!(expr)) { fprintf(stderr, "FAILED: %s (line %d)\n", #expr, __LINE__); return 1; } } while (0)
static HSTRING string(const wchar_t *text) {
    HSTRING result = nullptr;
    if (FAILED(WindowsCreateString(text, static_cast<UINT32>(wcslen(text)), &result))) abort();
    return result;
}

int main() {
    CHECK(SUCCEEDED(RoInitialize(RO_INIT_MULTITHREADED)));
    HMODULE module = LoadLibraryW(L"api_information_proxy.dll");
    CHECK(module);
    using GetFactory = HRESULT (WINAPI *)(HSTRING, IActivationFactory **);
    auto get = reinterpret_cast<GetFactory>(GetProcAddress(module, "DllGetActivationFactory"));
    CHECK(get);
    HSTRING name = string(L"Windows.Foundation.Metadata.ApiInformation");
    IActivationFactory *factory = nullptr;
    CHECK(get(name, &factory) == S_OK);
    IApiInformationStatics *info = nullptr;
    CHECK(factory->QueryInterface(__uuidof(IApiInformationStatics), reinterpret_cast<void **>(&info)) == S_OK);
    IUnknown *identity1 = nullptr, *identity2 = nullptr;
    CHECK(factory->QueryInterface(__uuidof(IUnknown), reinterpret_cast<void **>(&identity1)) == S_OK);
    CHECK(info->QueryInterface(__uuidof(IUnknown), reinterpret_cast<void **>(&identity2)) == S_OK);
    CHECK(identity1 == identity2);
    identity1->Release(); identity2->Release();
    HSTRING lamp = string(L"Windows.Devices.Lights.LampArray");
    HSTRING property = string(L"IsAvailable");
    boolean value = true;
    CHECK(info->IsPropertyPresent(lamp, property, &value) == S_OK && !value);
    CHECK(info->IsPropertyPresent(lamp, property, nullptr) == E_POINTER);
    HSTRING other = string(L"UnrelatedProperty");
    CHECK(info->IsPropertyPresent(lamp, other, &value) == E_NOTIMPL);
    HSTRING contract = string(L"Windows.Foundation.UniversalApiContract");
    CHECK(info->IsApiContractPresentByMajor(contract, 10, &value) == S_OK && value);
    CHECK(info->IsApiContractPresentByMajor(contract, 11, &value) == S_OK && !value);
    for (HSTRING s : {name, lamp, property, other, contract}) WindowsDeleteString(s);
    info->Release(); factory->Release();
    HMODULE sensor = LoadLibraryW(L"mfsensorgroup.dll");
    CHECK(sensor);
    using CreateMonitor = HRESULT (WINAPI *)(void *, void **);
    auto create = reinterpret_cast<CreateMonitor>(GetProcAddress(sensor, "MFCreateSensorActivityMonitor"));
    CHECK(create);
    int dummy = 1;
    void *monitor = &dummy;
    CHECK(create(&dummy, &monitor) == E_NOTIMPL && !monitor);
    CHECK(create(nullptr, &monitor) == E_INVALIDARG && !monitor);
    CHECK(create(&dummy, nullptr) == E_POINTER);
    FreeLibrary(sensor);
    FreeLibrary(module);
    RoUninitialize();
    puts("PASS: absent lighting property, forwarded queries, COM identity, unsupported sensor monitor");
    return 0;
}
