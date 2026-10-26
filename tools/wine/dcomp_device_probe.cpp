/* Isolate the next G HUB compositor requirements from WinUI startup.
 * Usage: dcomp_device_probe.exe [dcomp DLL path] [rs5|vb] [FrameworkUdk DLL]
 * Run under a copied Wine prefix and Xvfb, never the live desktop.
 * This probes device/batch synchronization only; it does not prove rendering.
 * Private IIDs/slots were recovered from matching FrameworkUdk PDB/disassembly.
 */
#include <windows.h>
#include <dcomp.h>
#include <cstdio>
#include <cstring>

static const IID partner_rs5 = {0x25682ec4, 0x73a4, 0x4022, {0xa0, 0x4f, 0x1b, 0xbe, 0x6d, 0xc3, 0xe1, 0xf0}};
static const IID partner_vb = {0x28d6ad3d, 0xee2a, 0x4bcd, {0x94, 0x19, 0x7d, 0x54, 0x80, 0x04, 0x35, 0xb1}};
static const IID restricted = {0x6b556968, 0x2d25, 0x11e2, {0x99, 0xf6, 0x8b, 0xc3, 0x60, 0x88, 0x70, 0x9b}};

template <class T> static T method(IUnknown *object, unsigned slot)
{
    return reinterpret_cast<T>((*reinterpret_cast<void ***>(object))[slot]);
}

static bool report(const char *operation, HRESULT hr)
{
    std::fprintf(stderr, "%s: %08lx\n", operation, static_cast<unsigned long>(hr));
    return SUCCEEDED(hr);
}

static bool probe_shared_visual_handles(HMODULE module, HMODULE framework)
{
    using CreateShared = HRESULT(WINAPI *)(HANDLE *);
    auto create = reinterpret_cast<CreateShared>(
        reinterpret_cast<void *>(GetProcAddress(module, MAKEINTRESOURCEA(1040))));
    // Explicit export ordinals prevent the linker from assigning a public
    // factory to a different private ordinal with an incompatible signature.
    if (!create || !GetProcAddress(module, MAKEINTRESOURCEA(1038)) ||
        GetProcAddress(module, MAKEINTRESOURCEA(1039)) ||
        GetProcAddress(module, MAKEINTRESOURCEA(1020)) != GetProcAddress(module, "DCompositionCreateDevice") ||
        GetProcAddress(module, MAKEINTRESOURCEA(1021)) != GetProcAddress(module, "DCompositionCreateDevice2") ||
        GetProcAddress(module, MAKEINTRESOURCEA(1022)) != GetProcAddress(module, "DCompositionCreateDevice3"))
    {
        std::fputs("FAIL: incompatible composition export ordinals\n", stderr);
        return false;
    }
    if (create(nullptr) != E_POINTER)
        return false;
    HANDLE handle = INVALID_HANDLE_VALUE;
    HRESULT hr = create(&handle);
    std::fprintf(stderr, "Create shared visual: %08lx handle=%p\n", hr, handle);
    if (FAILED(hr) || !handle)
        return false;
    if (!CloseHandle(handle)) return false;
    if (framework)
    {
        auto wrapped = reinterpret_cast<CreateShared>(reinterpret_cast<void *>(
            GetProcAddress(framework, "DCompPrivates_CreateSharedVisualHandle")));
        if (!wrapped)
            return false;
        handle = INVALID_HANDLE_VALUE;
        hr = wrapped(&handle);
        std::fprintf(stderr, "Native FrameworkUdk shared visual: %08lx handle=%p\n", hr, handle);
        if (FAILED(hr) || !handle)
            return false;
        if (!CloseHandle(handle)) return false;
    }
    return true;
}

static bool probe(HMODULE module, HMODULE framework, const char *name, bool vb)
{
    using Create = HRESULT(WINAPI *)(IUnknown *, REFIID, void **);
    using Batch = HRESULT(WINAPI *)(IUnknown *, DWORD *);
    using SetEvent = HRESULT(WINAPI *)(IUnknown *, HANDLE);
    using CreateBatch = HRESULT(WINAPI *)(IUnknown *, IUnknown **);
    auto create = reinterpret_cast<Create>(reinterpret_cast<void *>(GetProcAddress(module, name)));
    IDCompositionDesktopDevice *device = nullptr;
    IUnknown *batch = nullptr, *events = nullptr, *identity = nullptr, *other_identity = nullptr;
    IUnknown *wrapped = nullptr;
    auto set_event = framework ? reinterpret_cast<SetEvent>(reinterpret_cast<void *>(
                                     GetProcAddress(framework, "DCompPrivates_SetCommitCompletionEvent")))
                               : nullptr;
    auto create_batch = framework ? reinterpret_cast<CreateBatch>(reinterpret_cast<void *>(
                                        GetProcAddress(framework, "DCompPrivates_CreateCompositionBatchQuery")))
                                  : nullptr;
    auto register_event = [&](HANDLE handle)
    { return framework ? set_event(device, handle) : method<SetEvent>(events, 8)(events, handle); };
    HANDLE event = nullptr, alias = nullptr, wrong_type = nullptr;
    DWORD current = 0xdeadbeef, confirmed = 0xdeadbeef, wait;
    bool ok = false;
    std::fprintf(stderr, "\n%s / %s batch ABI\n", name, vb ? "vb" : "rs5");
    if (!create)
    {
        std::fprintf(stderr, "Missing export, error=%lu\n", GetLastError());
        return false;
    }
    if (framework && (!set_event || !create_batch))
    {
        std::fputs("Missing FrameworkUdk exports\n", stderr);
        return false;
    }
    if (!report("Create desktop device",
                create(nullptr, __uuidof(IDCompositionDesktopDevice), reinterpret_cast<void **>(&device))) ||
        !device)
        goto done;
    if (!report("QI batch partner",
                device->QueryInterface(vb ? partner_vb : partner_rs5, reinterpret_cast<void **>(&batch))) ||
        !batch)
        goto done;
    if (!report("QI restricted device", device->QueryInterface(restricted, reinterpret_cast<void **>(&events))) ||
        !events)
        goto done;
    if (!report("QI device identity",
                device->QueryInterface(__uuidof(IUnknown), reinterpret_cast<void **>(&identity))) ||
        !identity)
        goto done;
    if (!report("QI batch identity",
                batch->QueryInterface(__uuidof(IUnknown), reinterpret_cast<void **>(&other_identity))) ||
        !other_identity)
        goto done;
    if (identity != other_identity)
    {
        std::fputs("FAIL: split COM identity\n", stderr);
        goto done;
    }
    other_identity->Release();
    other_identity = nullptr;
    if (!report("QI event identity",
                events->QueryInterface(__uuidof(IUnknown), reinterpret_cast<void **>(&other_identity))) ||
        !other_identity)
        goto done;
    if (identity != other_identity)
    {
        std::fputs("FAIL: split event COM identity\n", stderr);
        goto done;
    }
    if (!report("GetCurrentBatchID", method<Batch>(batch, vb ? 45 : 55)(batch, &current)))
        goto done;
    if (!report("GetLastConfirmedBatchId", method<Batch>(batch, vb ? 46 : 56)(batch, &confirmed)))
        goto done;
    if (current == 0xdeadbeef || confirmed == 0xdeadbeef)
    {
        std::fputs("FAIL: batch query left output untouched\n", stderr);
        goto done;
    }
    std::fprintf(stderr, "Before commit: current=%lu confirmed=%lu\n", current, confirmed);
    if (framework && (!report("Native FrameworkUdk batch wrapper", create_batch(device, &wrapped)) || !wrapped))
        goto done;
    event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!event)
        goto done;
    if (!report("SetCommitCompletionEvent", register_event(event)))
        goto done;
    wrong_type = CreateMutexW(nullptr, FALSE, nullptr);
    if (!wrong_type || SUCCEEDED(register_event(wrong_type)))
    {
        std::fputs("FAIL: accepted non-event handle\n", stderr);
        goto done;
    }
    CloseHandle(wrong_type);
    wrong_type = nullptr;
    if (!DuplicateHandle(GetCurrentProcess(), event, GetCurrentProcess(), &alias, 0, FALSE, DUPLICATE_SAME_ACCESS))
        goto done;
    CloseHandle(event);
    event = alias;
    alias = nullptr;
    if (!report("Commit after closing registration handle", device->Commit()))
        goto done;
    // Bound the observation before calling any potentially unbounded wait API.
    wait = WaitForSingleObject(event, 5000);
    std::fprintf(stderr, "Commit event wait: %lu\n", wait);
    if (wait != WAIT_OBJECT_0)
        goto done;
    if (!report("WaitForCommitCompletion", device->WaitForCommitCompletion()))
        goto done;
    if (!report("GetCurrentBatchID", method<Batch>(batch, vb ? 45 : 55)(batch, &current)))
        goto done;
    if (!report("GetLastConfirmedBatchId", method<Batch>(batch, vb ? 46 : 56)(batch, &confirmed)))
        goto done;
    std::fprintf(stderr, "After commit: current=%lu confirmed=%lu\n", current, confirmed);
    if (wrapped)
    {
        DWORD wrapper_current = 0xdeadbeef, wrapper_confirmed = 0xdeadbeef;
        if (!report("Native wrapper current", method<Batch>(wrapped, 3)(wrapped, &wrapper_current)) ||
            !report("Native wrapper confirmed", method<Batch>(wrapped, 4)(wrapped, &wrapper_confirmed)) ||
            wrapper_current != current || wrapper_confirmed != confirmed)
            goto done;
    }
    if (!report("Unregister commit event", register_event(nullptr)))
        goto done;
    ResetEvent(event);
    if (!report("Commit without event", device->Commit()) || WaitForSingleObject(event, 0) != WAIT_TIMEOUT)
        goto done;
    CloseHandle(event);
    event = CreateEventW(nullptr, FALSE, TRUE, nullptr);
    if (!event || !report("Register signaled auto-reset event", register_event(event)))
        goto done;
    if (WaitForSingleObject(event, 0) != WAIT_OBJECT_0 || WaitForSingleObject(event, 0) != WAIT_TIMEOUT)
    {
        std::fputs("FAIL: registration consumed or duplicated event signal\n", stderr);
        goto done;
    }
    if (!report("Commit with auto-reset event", device->Commit()) || WaitForSingleObject(event, 5000) != WAIT_OBJECT_0)
        goto done;
    ok = true;
done:
    if (wrapped)
        wrapped->Release();
    if (wrong_type)
        CloseHandle(wrong_type);
    if (alias)
        CloseHandle(alias);
    if (other_identity)
        other_identity->Release();
    if (identity)
        identity->Release();
    if (events)
        events->Release();
    if (batch)
        batch->Release();
    if (device)
        device->Release();
    if (event)
        CloseHandle(event);
    return ok;
}

int main(int argc, char **argv)
{
    bool vb = argc >= 3 && !std::strcmp(argv[2], "vb");
    if (argc > 4 || (argc >= 3 && !vb && std::strcmp(argv[2], "rs5")))
        return 2;
    std::setbuf(stderr, nullptr);
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(hr))
        return 2;
    HMODULE module = LoadLibraryA(argc > 1 ? argv[1] : "dcomp.dll");
    if (!module)
    {
        std::fprintf(stderr, "Load dcomp failed: error=%lu\n", GetLastError());
        CoUninitialize();
        return 2;
    }
    HMODULE framework = argc == 4 ? LoadLibraryExA(argv[3], nullptr, LOAD_WITH_ALTERED_SEARCH_PATH) : nullptr;
    if (argc == 4 && !framework)
    {
        std::fprintf(stderr, "Load FrameworkUdk: %lu\n", GetLastError());
        FreeLibrary(module);
        CoUninitialize();
        return 2;
    }
    bool v2 = probe(module, framework, "DCompositionCreateDevice2", vb);
    bool v3 = probe(module, framework, "DCompositionCreateDevice3", vb);
    bool shared = probe_shared_visual_handles(module, framework);
    if (framework)
        FreeLibrary(framework);
    FreeLibrary(module);
    CoUninitialize();
    std::fprintf(stderr, "Device/batch and shared-handle probe: %s; no pixels tested\n",
                 v2 && v3 && shared ? "PASS" : "BLOCKED");
    return v2 && v3 && shared ? 0 : 1;
}
