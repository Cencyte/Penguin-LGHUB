/* Independent COM ABI tests plus controlled KMT topology at the DXGI import
 * boundary. Synthetic adapters never enter WineD3D or the host display driver.
 * This proves notification/lifetime behavior, not physical GPU hotplug support. */
#include <windows.h>
#include <dxgi1_6.h>
#include <cstdio>
#include <cstring>

static LONG checks, failures;
#define CHECK(x) do { InterlockedIncrement(&checks); if (!(x)) { InterlockedIncrement(&failures); \
    std::printf("FAIL %d: %s\n", __LINE__, #x); } } while (0)

struct AdapterInfo { UINT handle; LUID luid; ULONG sources; BOOL precise; };
struct EnumAdapters { ULONG count; AdapterInfo *adapters; };
static_assert(sizeof(AdapterInfo) == 20 && sizeof(EnumAdapters) == 16, "KMT ABI");
static SRWLOCK topology_lock = SRWLOCK_INIT;
static ULONG topology = 2, sources = 1;
static bool reverse_order, enumeration_error;
static LONG opened, closed;
static HANDLE entered, proceed;
static LONG block_next;

static LONG WINAPI fake_enum(EnumAdapters *desc)
{
    if (InterlockedCompareExchange(&block_next, 0, 1) == 1)
    {
        SetEvent(entered);
        WaitForSingleObject(proceed, INFINITE);
    }
    AcquireSRWLockShared(&topology_lock);
    LONG result = 0;
    if (enumeration_error) result = (LONG)0xc0000001;
    else if (!desc->adapters) desc->count = 8;
    else if (desc->count < topology) result = (LONG)0xc0000023;
    else
    {
        desc->count = topology;
        for (ULONG i = 0; i < topology; ++i)
        {
            ULONG id = reverse_order ? topology - i : i + 1;
            desc->adapters[i] = {0xf0000000u | id, {id, 42}, sources, FALSE};
            InterlockedIncrement(&opened);
        }
    }
    ReleaseSRWLockShared(&topology_lock);
    return result;
}

static LONG WINAPI fake_close(const UINT *handle)
{
    CHECK((*handle & 0xf0000000u) == 0xf0000000u);
    InterlockedIncrement(&closed);
    return 0;
}

struct ImportHook
{
    ULONG_PTR *slot = nullptr, original = 0;
    bool install(HMODULE module, const char *name, ULONG_PTR replacement)
    {
        auto base = reinterpret_cast<BYTE *>(module);
        auto dos = reinterpret_cast<IMAGE_DOS_HEADER *>(base);
        auto nt = reinterpret_cast<IMAGE_NT_HEADERS *>(base + dos->e_lfanew);
        auto directory = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
        if (!directory.VirtualAddress) return false;
        auto imports = reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR *>(base + directory.VirtualAddress);
        for (; imports->Name; ++imports)
        {
            if (!imports->OriginalFirstThunk) continue;
            auto names = reinterpret_cast<IMAGE_THUNK_DATA *>(base + imports->OriginalFirstThunk);
            auto slots = reinterpret_cast<IMAGE_THUNK_DATA *>(base + imports->FirstThunk);
            for (; names->u1.AddressOfData; ++names, ++slots)
            {
                if (IMAGE_SNAP_BY_ORDINAL(names->u1.Ordinal)) continue;
                auto entry = reinterpret_cast<IMAGE_IMPORT_BY_NAME *>(base + names->u1.AddressOfData);
                if (std::strcmp(entry->Name, name)) continue;
                DWORD protection;
                slot = &slots->u1.Function;
                if (!VirtualProtect(slot, sizeof(*slot), PAGE_READWRITE, &protection)) return false;
                original = *slot;
                *slot = replacement;
                VirtualProtect(slot, sizeof(*slot), protection, &protection);
                return true;
            }
        }
        return false;
    }
    ~ImportHook()
    {
        if (!slot || !original) return;
        DWORD protection;
        if (VirtualProtect(slot, sizeof(*slot), PAGE_READWRITE, &protection))
        {
            *slot = original;
            VirtualProtect(slot, sizeof(*slot), protection, &protection);
        }
    }
};

static IDXGIFactory7 *factory()
{
    IDXGIFactory7 *result = nullptr;
    HRESULT hr = CreateDXGIFactory1(__uuidof(IDXGIFactory7), reinterpret_cast<void **>(&result));
    CHECK(hr == S_OK && result);
    return result;
}

static void contract_tests()
{
    auto f = factory(), other = factory();
    if (!f || !other) ExitProcess(2);
    HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    HANDLE mutex = CreateMutexW(nullptr, FALSE, nullptr);
    HANDLE modify = nullptr, sync = nullptr;
    DWORD cookie = 0xfeed, duplicate = 0, foreign = 0;
    CHECK(f->IsCurrent());
    CHECK(FAILED(f->RegisterAdaptersChangedEvent(event, nullptr)));
    CHECK(FAILED(f->RegisterAdaptersChangedEvent(nullptr, &cookie)) && cookie == 0xfeed);
    CHECK(FAILED(f->RegisterAdaptersChangedEvent(INVALID_HANDLE_VALUE, &cookie)) && cookie == 0xfeed);
    CHECK(FAILED(f->RegisterAdaptersChangedEvent(mutex, &cookie)) && cookie == 0xfeed);
    CHECK(DuplicateHandle(GetCurrentProcess(), event, GetCurrentProcess(), &modify, EVENT_MODIFY_STATE, FALSE, 0));
    CHECK(DuplicateHandle(GetCurrentProcess(), event, GetCurrentProcess(), &sync, SYNCHRONIZE, FALSE, 0));
    CHECK(FAILED(f->RegisterAdaptersChangedEvent(sync, &cookie)) && cookie == 0xfeed);
    CHECK(f->RegisterAdaptersChangedEvent(modify, &cookie) == S_OK && cookie);
    CHECK(f->RegisterAdaptersChangedEvent(event, &duplicate) == S_OK && duplicate != cookie);
    CHECK(other->RegisterAdaptersChangedEvent(event, &foreign) == S_OK && foreign != cookie && foreign != duplicate);
    CHECK(FAILED(other->UnregisterAdaptersChangedEvent(cookie)));
    CHECK(FAILED(f->UnregisterAdaptersChangedEvent(0)));
    CHECK(WaitForSingleObject(event, 1200) == WAIT_TIMEOUT);
    CHECK(f->IsCurrent());
    CHECK(f->UnregisterAdaptersChangedEvent(duplicate) == S_OK);
    CHECK(FAILED(f->UnregisterAdaptersChangedEvent(duplicate)));
    CloseHandle(modify);
    CloseHandle(sync);
    CHECK(f->UnregisterAdaptersChangedEvent(cookie) == S_OK);
    CHECK(other->UnregisterAdaptersChangedEvent(foreign) == S_OK);
    SetEvent(event);
    CHECK(f->RegisterAdaptersChangedEvent(event, &cookie) == S_OK);
    CHECK(WaitForSingleObject(event, 0) == WAIT_OBJECT_0); // registration never resets callers' events
    CHECK(f->UnregisterAdaptersChangedEvent(cookie) == S_OK);
    CHECK(f->Release() == 0);
    CHECK(other->Release() == 0);
    CloseHandle(event);
    CloseHandle(mutex);
    std::printf("REAL KMT contract stage: %ld checks, %ld failures\n", checks, failures);
}

static void set_topology(ULONG count, bool reverse = false, ULONG source_count = 1, bool error = false)
{
    AcquireSRWLockExclusive(&topology_lock);
    topology = count;
    reverse_order = reverse;
    sources = source_count;
    enumeration_error = error;
    ReleaseSRWLockExclusive(&topology_lock);
}

static DWORD WINAPI registration_worker(void *context)
{
    auto f = static_cast<IDXGIFactory7 *>(context);
    for (unsigned i = 0; i < 32; ++i)
    {
        HANDLE event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
        DWORD cookie = 0;
        CHECK(f->RegisterAdaptersChangedEvent(event, &cookie) == S_OK && cookie);
        CloseHandle(event);
        CHECK(f->UnregisterAdaptersChangedEvent(cookie) == S_OK);
    }
    return 0;
}

static DWORD WINAPI release_factory(void *context)
{
    CHECK(static_cast<IDXGIFactory7 *>(context)->Release() == 0);
    return 0;
}

static void topology_tests()
{
    ImportHook enumeration_hook, close_hook;
    HMODULE dxgi = GetModuleHandleW(L"dxgi.dll");
    bool hooked = enumeration_hook.install(dxgi, "D3DKMTEnumAdapters2", reinterpret_cast<ULONG_PTR>(fake_enum));
    CHECK(hooked);
    if (!hooked) return;
    hooked = close_hook.install(dxgi, "D3DKMTCloseAdapter", reinterpret_cast<ULONG_PTR>(fake_close));
    CHECK(hooked);
    if (!hooked) return;

    auto f = factory();
    if (!f) return;
    HANDLE manual = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    HANDLE automatic = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    HANDLE original = CreateEventW(nullptr, TRUE, FALSE, nullptr), retained = nullptr;
    DWORD cm = 0, ca = 0, cr = 0;
    CHECK(DuplicateHandle(GetCurrentProcess(), original, GetCurrentProcess(), &retained, SYNCHRONIZE, FALSE, 0));
    CHECK(f->RegisterAdaptersChangedEvent(manual, &cm) == S_OK);
    CHECK(f->RegisterAdaptersChangedEvent(automatic, &ca) == S_OK);
    CHECK(f->RegisterAdaptersChangedEvent(original, &cr) == S_OK);
    CloseHandle(original); // notification must retain its own handle

    set_topology(2, true, 5); // reordered GPUs and a different monitor/source count
    CHECK(WaitForSingleObject(manual, 1200) == WAIT_TIMEOUT);
    CHECK(f->IsCurrent());
    set_topology(2, false, 1, true);
    CHECK(!f->IsCurrent()); // failed observation, not a proven topology change
    CHECK(WaitForSingleObject(manual, 1200) == WAIT_TIMEOUT);
    set_topology(2);
    CHECK(f->IsCurrent());

    set_topology(3); // genuine adapter-set change, detected by asynchronous polling
    CHECK(WaitForSingleObject(manual, 4000) == WAIT_OBJECT_0);
    CHECK(WaitForSingleObject(automatic, 0) == WAIT_OBJECT_0);
    CHECK(WaitForSingleObject(automatic, 0) == WAIT_TIMEOUT);
    CHECK(WaitForSingleObject(retained, 0) == WAIT_OBJECT_0);
    CHECK(!f->IsCurrent());
    auto fresh = factory();
    if (!fresh) ExitProcess(2);
    CHECK(fresh->IsCurrent());
    CHECK(f->UnregisterAdaptersChangedEvent(cm) == S_OK);
    CHECK(f->UnregisterAdaptersChangedEvent(cr) == S_OK);
    ResetEvent(manual);
    set_topology(2);
    CHECK(WaitForSingleObject(automatic, 4000) == WAIT_OBJECT_0);
    CHECK(WaitForSingleObject(manual, 0) == WAIT_TIMEOUT);
    CHECK(!f->IsCurrent()); // returning to the original set does not revive the old factory
    CHECK(!fresh->IsCurrent());
    CHECK(f->UnregisterAdaptersChangedEvent(ca) == S_OK);
    CHECK(f->RegisterAdaptersChangedEvent(manual, &cm) == S_OK);
    CHECK(WaitForSingleObject(manual, 0) == WAIT_OBJECT_0); // stale at registration
    CHECK(f->UnregisterAdaptersChangedEvent(cm) == S_OK);
    CHECK(f->Release() == 0);
    CHECK(fresh->Release() == 0);

    set_topology(0);
    f = factory();
    if (!f) ExitProcess(2);
    CHECK(f->IsCurrent());
    CHECK(f->RegisterAdaptersChangedEvent(automatic, &ca) == S_OK);
    set_topology(1);
    CHECK(WaitForSingleObject(automatic, 4000) == WAIT_OBJECT_0);
    CHECK(!f->IsCurrent());
    CHECK(f->Release() == 0); // live registration is cleaned up

    f = factory();
    if (!f) ExitProcess(2);
    HANDLE workers[8];
    for (auto &worker : workers) worker = CreateThread(nullptr, 0, registration_worker, f, 0, nullptr);
    CHECK(WaitForMultipleObjects(8, workers, TRUE, 15000) == WAIT_OBJECT_0);
    for (auto worker : workers) CloseHandle(worker);
    CHECK(f->Release() == 0);

    // Release must wait for an already-running enumeration callback, and then
    // never access freed state. The import hook remains installed until drained.
    f = factory();
    if (!f) ExitProcess(2);
    ResetEvent(manual);
    CHECK(f->RegisterAdaptersChangedEvent(manual, &cm) == S_OK);
    entered = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    proceed = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    InterlockedExchange(&block_next, 1);
    CHECK(WaitForSingleObject(entered, 4000) == WAIT_OBJECT_0);
    HANDLE releaser = CreateThread(nullptr, 0, release_factory, f, 0, nullptr);
    CHECK(WaitForSingleObject(releaser, 100) == WAIT_TIMEOUT);
    SetEvent(proceed);
    CHECK(WaitForSingleObject(releaser, 4000) == WAIT_OBJECT_0);
    CloseHandle(releaser);
    CloseHandle(entered);
    CloseHandle(proceed);
    CHECK(WaitForSingleObject(manual, 1200) == WAIT_TIMEOUT);
    CHECK(opened == closed);
    CloseHandle(manual);
    CloseHandle(automatic);
    CloseHandle(retained);
    std::printf("SYNTHETIC KMT stage: %ld opened, %ld closed\n", opened, closed);
}

int main()
{
    contract_tests();
    topology_tests();
    std::printf("Adapter notifications: %ld checks, %ld failures\n", checks, failures);
    return failures ? 1 : 0;
}
