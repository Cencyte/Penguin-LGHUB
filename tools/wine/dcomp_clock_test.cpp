/* Headless API/pacing regression for the laboratory software compositor clock.
 * Tests NTSTATUS results, cancellation, timeout, APC and handle lifetime.
 * This does not establish hardware VBlank synchronization. */
#include <windows.h>
#include <cstdio>

using Clock = DWORD (WINAPI *)(UINT, const HANDLE *, DWORD);
static unsigned checks, failures;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; std::printf("FAIL line %d: %s\n", __LINE__, #x); } } while (0)
constexpr DWORD invalid_parameter = 0xc000000d, invalid_handle = 0xc0000008;
constexpr DWORD timeout_status = 0x102, user_apc = 0xc0;
static volatile LONG apcs;
static void CALLBACK apc(ULONG_PTR) { InterlockedIncrement(&apcs); }
struct ThreadWait { Clock clock; HANDLE ready; DWORD result; };
static DWORD WINAPI wait_thread(void *parameter)
{
    auto *wait = static_cast<ThreadWait *>(parameter);
    SetEvent(wait->ready);
    wait->result = wait->clock(0, nullptr, INFINITE);
    return 0;
}
static DWORD WINAPI abandon(void *parameter)
{
    return WaitForSingleObject(static_cast<HANDLE>(parameter), 1000) == WAIT_OBJECT_0 ? 0 : 1;
}
int main()
{
    HMODULE module = LoadLibraryW(L"dcomp.dll");
    CHECK(module);
    if (!module) return 1;
    Clock clock = reinterpret_cast<Clock>(GetProcAddress(module, "DCompositionWaitForCompositorClock"));
    CHECK(clock);
    if (!clock) return 1;
    auto ordinal = reinterpret_cast<Clock>(GetProcAddress(module, MAKEINTRESOURCEA(1104)));
    CHECK(ordinal == clock);
    HANDLE events[32];
    for (auto &event : events) { event = CreateEventW(nullptr, TRUE, FALSE, nullptr); CHECK(event); }
    CHECK(clock(33, events, 0) == invalid_parameter);
    CHECK(clock(1, nullptr, 0) == invalid_parameter);
    HANDLE bad = nullptr;
    CHECK(clock(1, &bad, 0) == invalid_handle);
    // A negative pseudo-handle must not turn into an infinite clock wait.
    bad = reinterpret_cast<HANDLE>(static_cast<INT_PTR>(-123));
    CHECK(clock(1, &bad, 0) == invalid_handle);
    CHECK(SetEvent(events[0]));
    CHECK(clock(1, events, INFINITE) == 0);
    CHECK(WaitForSingleObject(events[0], 0) == WAIT_OBJECT_0); // Manual-reset event retained.
    CHECK(ResetEvent(events[0]));
    CHECK(SetEvent(events[1]));
    CHECK(clock(2, events, INFINITE) == 1);
    CHECK(ResetEvent(events[1]));
    CHECK(SetEvent(events[31]));
    CHECK(clock(32, events, INFINITE) == 31);
    CHECK(ResetEvent(events[31]));
    CHECK(clock(32, events, 1000) == 32); // Clock follows all caller handles.
    HANDLE automatic = CreateEventW(nullptr, FALSE, TRUE, nullptr);
    CHECK(automatic);
    CHECK(clock(1, &automatic, INFINITE) == 0);
    CHECK(WaitForSingleObject(automatic, 0) == WAIT_TIMEOUT);
    CHECK(CloseHandle(automatic));
    unsigned timeouts = 0;
    for (unsigned i = 0; i < 16; ++i)
    {
        DWORD result = clock(0, nullptr, 0);
        CHECK(result == timeout_status || result == 0); // Timer can expire after scheduling delay.
        if (result == timeout_status) ++timeouts;
    }
    CHECK(timeouts > 0);
    CHECK(clock(0, reinterpret_cast<const HANDLE *>(1), 1000) == 0); // Count zero ignores pointer.
    CHECK(QueueUserAPC(apc, GetCurrentThread(), 0));
    CHECK(clock(0, nullptr, INFINITE) == user_apc);
    CHECK(apcs == 1);
    HANDLE mutex = CreateMutexW(nullptr, FALSE, nullptr);
    HANDLE thread = CreateThread(nullptr, 0, abandon, mutex, 0, nullptr);
    CHECK(mutex && thread);
    CHECK(WaitForSingleObject(thread, 2000) == WAIT_OBJECT_0);
    DWORD exit_code = 99;
    CHECK(GetExitCodeThread(thread, &exit_code) && exit_code == 0);
    CHECK(CloseHandle(thread));
    CHECK(clock(1, &mutex, 1000) == WAIT_ABANDONED_0);
    CHECK(ReleaseMutex(mutex));
    CHECK(CloseHandle(mutex));
    ThreadWait waits[4] = {};
    HANDLE threads[4];
    for (unsigned i = 0; i < 4; ++i)
    {
        waits[i] = {clock, CreateEventW(nullptr, TRUE, FALSE, nullptr), ~0u};
        threads[i] = CreateThread(nullptr, 0, wait_thread, &waits[i], 0, nullptr);
        CHECK(waits[i].ready && threads[i]);
    }
    CHECK(WaitForMultipleObjects(4, threads, TRUE, 2000) == WAIT_OBJECT_0);
    for (unsigned i = 0; i < 4; ++i)
    {
        CHECK(waits[i].result == 0);
        CHECK(CloseHandle(threads[i]));
        CHECK(CloseHandle(waits[i].ready));
    }
    // A genuine clock wait must not spin immediately with fabricated success.
    LARGE_INTEGER start, end, frequency;
    DEVMODEW mode = {}; mode.dmSize = sizeof(mode);
    unsigned rate = 60;
    if (EnumDisplaySettingsW(nullptr, ENUM_CURRENT_SETTINGS, &mode) && mode.dmDisplayFrequency > 1)
        rate = mode.dmDisplayFrequency;
    CHECK(QueryPerformanceFrequency(&frequency));
    DWORD handles_before = 0, handles_after = 0;
    CHECK(GetProcessHandleCount(GetCurrentProcess(), &handles_before));
    CHECK(QueryPerformanceCounter(&start));
    for (unsigned i = 0; i < 24; ++i) CHECK(clock(0, nullptr, 1000) == 0);
    CHECK(QueryPerformanceCounter(&end));
    double elapsed = static_cast<double>(end.QuadPart - start.QuadPart) / frequency.QuadPart;
    CHECK(elapsed >= 20.0 / rate);
    CHECK(elapsed < 10.0);
    CHECK(GetProcessHandleCount(GetCurrentProcess(), &handles_after));
    CHECK(handles_after == handles_before);
    for (auto event : events) CHECK(CloseHandle(event));
    auto unload = reinterpret_cast<HRESULT (WINAPI *)()>(GetProcAddress(module, "DllCanUnloadNow"));
    CHECK(unload && unload() == S_OK);
    CHECK(FreeLibrary(module));
    std::printf("Clock pacing: %.3f seconds for 24 waits, nominal %u Hz\n", elapsed, rate);
    std::printf("Compositor clock: %u checks, %u failures\n", checks, failures);
    return failures ? 1 : 0;
}
