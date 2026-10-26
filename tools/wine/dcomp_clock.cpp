/* Software frame clock for the experimental synchronous compositor.
 * Like Wine's DwmFlush pacing, align waits to QPC intervals at the primary
 * display frequency (60 Hz for virtual/default modes). This is not a hardware
 * VBlank implementation. No background thread, retained timer or DLL pinning.
 *
 * Native win32kbase reference: count <= 32; caller handles precede the clock
 * event in an alertable WaitAny. Return raw NTSTATUS, including the selected
 * handle index, timeout, abandonment and errors, rather than HRESULT/Win32.
 */
#include <windows.h>
#include <winternl.h>

extern "C" NTSTATUS NTAPI NtCreateTimer(HANDLE *, ACCESS_MASK, OBJECT_ATTRIBUTES *, ULONG);
extern "C" NTSTATUS NTAPI NtSetTimer(HANDLE, const LARGE_INTEGER *, void *, void *, BOOLEAN, LONG, BOOLEAN *);
extern "C" NTSTATUS NTAPI NtWaitForMultipleObjects(ULONG, const HANDLE *, ULONG, BOOLEAN, const LARGE_INTEGER *);

extern "C" DWORD WINAPI DCompositionWaitForCompositorClock(UINT count, const HANDLE *handles, DWORD timeout_ms)
{
    constexpr NTSTATUS invalid_parameter = static_cast<NTSTATUS>(0xc000000d);
    constexpr NTSTATUS unsuccessful = static_cast<NTSTATUS>(0xc0000001);
    HANDLE waits[33], timer = nullptr;
    LARGE_INTEGER counter, frequency, due, timeout;
    DEVMODEW mode = {};
    DWORD rate = 60;
    NTSTATUS status;

    if (count > 32 || (count && !handles)) return invalid_parameter;
    for (UINT i = 0; i < count; ++i) waits[i] = handles[i];
    mode.dmSize = sizeof(mode);
    if (EnumDisplaySettingsW(nullptr, ENUM_CURRENT_SETTINGS, &mode) && mode.dmDisplayFrequency > 1)
        rate = mode.dmDisplayFrequency;
    if (!QueryPerformanceFrequency(&frequency) || !QueryPerformanceCounter(&counter) ||
        frequency.QuadPart <= 0 || counter.QuadPart < 0) return unsuccessful;
    LONGLONG period = frequency.QuadPart / rate;
    if (!period) period = 1;
    LONGLONG remaining = period - counter.QuadPart % period;
    // Ceil the conversion so rounding cannot produce a zero-duration wait.
    due.QuadPart = -(remaining * 10000000 + frequency.QuadPart - 1) / frequency.QuadPart;
    status = NtCreateTimer(&timer, TIMER_ALL_ACCESS, nullptr, 0 /* NotificationTimer */);
    if (status < 0) return status;
    status = NtSetTimer(timer, &due, nullptr, nullptr, FALSE, 0, nullptr);
    if (status >= 0)
    {
        waits[count] = timer;
        timeout.QuadPart = -static_cast<LONGLONG>(timeout_ms) * 10000;
        status = NtWaitForMultipleObjects(count + 1, waits, 1 /* WaitAny */, TRUE,
                                         timeout_ms == INFINITE ? nullptr : &timeout);
    }
    NtClose(timer);
    return status;
}
