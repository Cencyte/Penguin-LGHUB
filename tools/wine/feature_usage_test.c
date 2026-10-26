/* Resolve the exact API-set from the native DirectManipulation delay import.
 * Link RecordFeatureUsage with dlltool's delay import library so the first
 * actual call also exercises the delay-load path, not just GetProcAddress. */
#include <windows.h>
#include <stdio.h>

__declspec(dllimport) void WINAPI RecordFeatureUsage(UINT32, UINT32, UINT32, const char *);
static DWORD WINAPI report_usage(void *argument)
{
    UINT32 id = (UINT32)(ULONG_PTR)argument;
    unsigned i;
    for (i = 0; i < 1000; ++i)
        RecordFeatureUsage(id, i & 7, 1, "ghub-feature-usage-regression");
    return 0;
}

int main(void)
{
    HMODULE api = LoadLibraryA("api-ms-win-core-featurestaging-l1-1-0.dll");
    HMODULE shcore = LoadLibraryA("shcore.dll");
    FARPROC entry = api ? GetProcAddress(api, "RecordFeatureUsage") : NULL;
    HANDLE threads[4];
    unsigned i;
    if (!entry || !shcore || entry != GetProcAddress(shcore, "RecordFeatureUsage"))
    {
        fprintf(stderr, "FAIL: missing or inconsistent API-set target, error=%lu\n", GetLastError());
        return 1;
    }
    /* Resolve once before concurrency; the following workload checks that
     * accepting independent usage reports does not need shared mutable state. */
    RecordFeatureUsage(0, 0, 0, "");
    RecordFeatureUsage(~0u, ~0u, ~0u, NULL);
    for (i = 0; i < 4; ++i)
    {
        threads[i] = CreateThread(NULL, 0, report_usage, (void *)(ULONG_PTR)(i + 1), 0, NULL);
        if (!threads[i]) return 2;
    }
    if (WaitForMultipleObjects(4, threads, TRUE, 10000) != WAIT_OBJECT_0) return 3;
    for (i = 0; i < 4; ++i) CloseHandle(threads[i]);
    FreeLibrary(api);
    FreeLibrary(shcore);
    puts("PASS: API-set identity, delay import, boundary arguments, 4000 threaded reports");
    return 0;
}
