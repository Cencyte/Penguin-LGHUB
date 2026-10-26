/* Composition input sink lifecycle and descriptor validation, x64 lab ABI.
 * Run on a private Xvfb display, with the matched Win32u/ntdll/server build.
 * This test does not establish composition hit testing or input dispatch.
 */
#define WIN32_NO_STATUS
#include <windows.h>
#include <winternl.h>
#undef WIN32_NO_STATUS
#include <ntstatus.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

struct route { UINT type, reserved; UINT64 window, data[5]; };
struct descriptor { UINT size, view; struct route routes[5]; UINT flags, reserved; };
typedef NTSTATUS (WINAPI *create_fn)(const void *, HANDLE *);
typedef NTSTATUS (WINAPI *query_fn)(HANDLE, void *);
typedef NTSTATUS (WINAPI *close_fn)(HANDLE);
typedef NTSTATUS (WINAPI *object_fn)(HANDLE, OBJECT_INFORMATION_CLASS, void *, ULONG, ULONG *);
typedef NTSTATUS (WINAPI *luid_fn)(LUID *);
static create_fn create_sink, create_implicit;
static query_fn query_luid, query_view;
static close_fn close_sink, close_nt;
static object_fn query_object;
static luid_fn allocate_luid;
static unsigned checks, failures;
#define CHECK(c) do { ++checks; if (!(c)) { ++failures; \
    fprintf(stderr, "FAIL line %u: %s error=%lu\n", __LINE__, #c, GetLastError()); } } while (0)
#define STATUS(c, expected) do { NTSTATUS s_ = (c); ++checks; if (s_ != (expected)) { ++failures; \
    fprintf(stderr, "FAIL line %u: %s status=%08lx expected=%08lx\n", \
        __LINE__, #c, (ULONG)s_, (ULONG)(expected)); } } while (0)

static struct descriptor descriptor(HWND window)
{
    struct descriptor result = {0};
    unsigned i;
    result.size = 0xe8;
    result.view = 0x12345678;
    for (i = 0; i < 4; ++i) {
        result.routes[i].type = 2;
        result.routes[i].window = (UINT_PTR)window;
    }
    return result;
}

static int load(void)
{
    HMODULE ui = LoadLibraryW(L"win32u.dll"), nt = GetModuleHandleW(L"ntdll.dll");
#define LOAD(module, name, symbol, type) name = (type)GetProcAddress(module, symbol); if (!name) return 0
    LOAD(ui, create_sink, "NtCreateCompositionInputSink", create_fn);
    LOAD(ui, create_implicit, "NtCreateImplicitCompositionInputSink", create_fn);
    LOAD(ui, query_luid, "NtQueryCompositionInputSinkLuid", query_fn);
    LOAD(ui, query_view, "NtQueryCompositionInputSinkViewId", query_fn);
    LOAD(ui, close_sink, "NtCloseCompositionInputSink", close_fn);
    LOAD(nt, close_nt, "NtClose", close_fn);
    LOAD(nt, query_object, "NtQueryObject", object_fn);
    LOAD(nt, allocate_luid, "NtAllocateLocallyUniqueId", luid_fn);
#undef LOAD
    return 1;
}

int wmain(int argc, WCHAR **argv)
{
    HWND window;
    HANDLE sink = NULL, second = NULL, duplicate = NULL, restricted = NULL, event, rejected;
    struct descriptor desc, bad;
    LUID id = {0}, again = {0}, other = {0};
    UINT view = 0;
    unsigned i;
    DWORD before, after, exit_code = STILL_ACTIVE, old_protect;
    BYTE info[1024];
    UNICODE_STRING *type = (UNICODE_STRING *)info;
    WCHAR path[MAX_PATH], command[2 * MAX_PATH];
    STARTUPINFOW startup = {sizeof(startup)};
    PROCESS_INFORMATION process;
    void *inaccessible;
    if (!load()) { fprintf(stderr, "Missing exports\n"); return 2; }
    if (argc > 1 && !wcscmp(argv[1], L"--child")) {
        sink = (HANDLE)(UINT_PTR)wcstoull(argv[2], NULL, 16);
        id.LowPart = wcstoul(argv[3], NULL, 16);
        id.HighPart = wcstoul(argv[4], NULL, 16);
        STATUS(query_luid(sink, &again), STATUS_SUCCESS);
        CHECK(!memcmp(&id, &again, sizeof(id)));
        STATUS(query_view(sink, &view), STATUS_SUCCESS);
        CHECK(view == 0x12345678);
        desc = descriptor((HWND)(UINT_PTR)wcstoull(argv[5], NULL, 16));
        STATUS(create_sink(&desc, &rejected), STATUS_ACCESS_DENIED);
        CHECK(rejected == INVALID_HANDLE_VALUE);
        STATUS(close_sink(sink), STATUS_SUCCESS);
        printf("child: %u checks, %u failures\n", checks, failures);
        return !!failures;
    }
    window = CreateWindowExW(WS_EX_NOACTIVATE, L"STATIC", L"sink-test", 0,
                            0, 0, 0, 0, HWND_MESSAGE, NULL, NULL, NULL);
    if (!window) return 2;
    desc = descriptor(window);
    if (argc > 1 && !wcscmp(argv[1], L"--disabled")) {
        STATUS(create_sink(&desc, &sink), STATUS_NOT_IMPLEMENTED);
        CHECK(sink == INVALID_HANDLE_VALUE);
        DestroyWindow(window);
        printf("disabled: %u checks, %u failures\n", checks, failures);
        return !!failures;
    }
    STATUS(create_sink(&desc, &sink), STATUS_SUCCESS);
    if (!sink || sink == INVALID_HANDLE_VALUE) return 1;
    STATUS(query_luid(sink, &id), STATUS_SUCCESS);
    CHECK(id.LowPart || id.HighPart);
    STATUS(query_luid(sink, &again), STATUS_SUCCESS);
    CHECK(!memcmp(&id, &again, sizeof(id)));
    STATUS(query_view(sink, &view), STATUS_SUCCESS);
    CHECK(view == desc.view);
    desc.view = 99;
    STATUS(query_view(sink, &view), STATUS_SUCCESS);
    CHECK(view == 0x12345678);
    STATUS(create_sink(&desc, &second), STATUS_SUCCESS);
    STATUS(query_luid(second, &other), STATUS_SUCCESS);
    CHECK(memcmp(&id, &other, sizeof(id)) != 0);
    STATUS(allocate_luid(&again), STATUS_SUCCESS);
    CHECK(memcmp(&id, &again, sizeof(id)) && memcmp(&other, &again, sizeof(id)));
    STATUS(close_nt(second), STATUS_SUCCESS);
    STATUS(query_luid(second, &again), STATUS_INVALID_HANDLE);
    STATUS(query_object(sink, ObjectTypeInformation, info, sizeof(info), NULL), STATUS_SUCCESS);
    CHECK(type->Length == 22 && !wcsncmp(type->Buffer, L"Composition", 11));
    STATUS(query_object(sink, ObjectBasicInformation, info, sizeof(info), NULL), STATUS_SUCCESS);
    CHECK(((ULONG *)info)[1] == (READ_CONTROL | 3)); /* Generic read/write, no delete or wait access. */
    CHECK(WaitForSingleObject(sink, 0) == WAIT_FAILED && GetLastError() == ERROR_ACCESS_DENIED);
    CHECK(DuplicateHandle(GetCurrentProcess(), sink, GetCurrentProcess(), &restricted, 0, FALSE, 0));
    STATUS(query_luid(restricted, &again), STATUS_ACCESS_DENIED);
    STATUS(close_nt(restricted), STATUS_SUCCESS);
    CHECK(DuplicateHandle(GetCurrentProcess(), sink, GetCurrentProcess(), &duplicate, 0, TRUE, DUPLICATE_SAME_ACCESS));
    STATUS(close_sink(sink), STATUS_SUCCESS);
    STATUS(query_luid(sink, &again), STATUS_INVALID_HANDLE);
    STATUS(query_luid(duplicate, &again), STATUS_SUCCESS);
    CHECK(!memcmp(&id, &again, sizeof(id)));
    GetModuleFileNameW(NULL, path, MAX_PATH);
    swprintf(command, sizeof(command) / sizeof(*command), L"\"%ls\" --child %llx %lx %lx %llx",
             path, (unsigned long long)(UINT_PTR)duplicate, id.LowPart, (ULONG)id.HighPart,
             (unsigned long long)(UINT_PTR)window);
    if (CreateProcessW(NULL, command, NULL, NULL, TRUE, 0, NULL, NULL, &startup, &process)) {
        CHECK(WaitForSingleObject(process.hProcess, 15000) == WAIT_OBJECT_0);
        CHECK(GetExitCodeProcess(process.hProcess, &exit_code) && exit_code == 0);
        if (exit_code == STILL_ACTIVE) TerminateProcess(process.hProcess, 1);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
    } else CHECK(FALSE);
    STATUS(query_luid(duplicate, &again), STATUS_SUCCESS);
    CHECK(!memcmp(&id, &again, sizeof(id)));
    event = CreateEventW(NULL, TRUE, FALSE, NULL);
    STATUS(query_luid(event, &again), STATUS_OBJECT_TYPE_MISMATCH);
    STATUS(close_sink(event), STATUS_SUCCESS); /* Native forwards to NtClose. */
    CHECK(!SetEvent(event) && GetLastError() == ERROR_INVALID_HANDLE);
    STATUS(query_luid(duplicate, NULL), STATUS_ACCESS_VIOLATION);
    STATUS(query_view(duplicate, (void *)1), STATUS_ACCESS_VIOLATION);
    desc = descriptor(window);
    STATUS(create_sink(NULL, &rejected), STATUS_INVALID_PARAMETER);
    CHECK(rejected == INVALID_HANDLE_VALUE);
    STATUS(create_sink(&desc, NULL), STATUS_INVALID_PARAMETER);
    STATUS(create_sink((void *)1, &rejected), STATUS_ACCESS_VIOLATION);
    CHECK(rejected == INVALID_HANDLE_VALUE);
    inaccessible = VirtualAlloc(NULL, 4096, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    CHECK(inaccessible != NULL);
    if (!inaccessible) return 2;
    CHECK(VirtualProtect(inaccessible, 4096, PAGE_NOACCESS, &old_protect));
    STATUS(create_sink(inaccessible, &rejected), STATUS_ACCESS_VIOLATION);
    STATUS(create_sink(&desc, inaccessible), STATUS_ACCESS_VIOLATION);
    VirtualFree(inaccessible, 0, MEM_RELEASE);
    bad = desc; bad.size = 4;
    STATUS(create_sink(&bad, &rejected), STATUS_NOT_SUPPORTED);
    CHECK(rejected == INVALID_HANDLE_VALUE);
    bad = desc; bad.routes[0].type = 5;
    STATUS(create_sink(&bad, &rejected), STATUS_INVALID_PARAMETER);
    bad = desc; bad.routes[0].type = 3;
    STATUS(create_sink(&bad, &rejected), STATUS_NOT_SUPPORTED);
    bad = desc; bad.routes[0].data[0] = 1;
    STATUS(create_sink(&bad, &rejected), STATUS_NOT_SUPPORTED);
    bad = desc; bad.routes[0].window = 0;
    STATUS(create_sink(&bad, &rejected), STATUS_INVALID_HANDLE);
    bad = desc; bad.routes[0].window |= (UINT64)1 << 40;
    STATUS(create_sink(&bad, &rejected), STATUS_INVALID_HANDLE);
    desc.size = sizeof(desc);
    desc.routes[4].type = 1;
    STATUS(create_implicit(&desc, &second), STATUS_SUCCESS);
    STATUS(query_view(second, &view), STATUS_SUCCESS);
    CHECK(view == desc.view);
    STATUS(close_sink(second), STATUS_SUCCESS);
    desc.flags = 2;
    STATUS(create_implicit(&desc, &rejected), STATUS_NOT_SUPPORTED);
    desc = descriptor(window);
    CHECK(GetProcessHandleCount(GetCurrentProcess(), &before));
    for (i = 0; i < 128; ++i) {
        STATUS(create_sink(&desc, &second), STATUS_SUCCESS);
        STATUS(close_sink(second), STATUS_SUCCESS);
        STATUS(create_sink(&desc, (void *)1), STATUS_ACCESS_VIOLATION);
    }
    CHECK(GetProcessHandleCount(GetCurrentProcess(), &after));
    CHECK(after == before);
    DestroyWindow(window);
    STATUS(create_sink(&desc, &rejected), STATUS_INVALID_HANDLE);
    CHECK(rejected == INVALID_HANDLE_VALUE);
    STATUS(query_luid(duplicate, &again), STATUS_SUCCESS);
    CHECK(!memcmp(&id, &again, sizeof(id))); /* Identity outlives the HWND. */
    STATUS(close_sink(duplicate), STATUS_SUCCESS);
    STATUS(close_sink(duplicate), STATUS_INVALID_HANDLE);
    printf("sink: %u checks, %u failures\n", checks, failures);
    return !!failures;
}
