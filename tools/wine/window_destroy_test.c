/* Lifecycle queries from callbacks, another thread and another process.
 * Run only in the private Xvfb Wine prefix through run_composition_input_test. */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

static BOOL (WINAPI *in_destroy)(HWND);
static unsigned checks, failures, destroyed, nc_destroyed, parent_notified, hook_seen;
static HWND root, child;
static BOOL veto, recursive_child, check_foreign;
#define CHECK(expr) do { ++checks; if (!(expr)) { ++failures; \
    fprintf(stderr, "FAIL line %u: %s error=%lu\n", __LINE__, #expr, GetLastError()); } } while (0)

struct query { HWND hwnd; BOOL expected; };
static DWORD WINAPI query_thread(void *value)
{
    const struct query *q = value;
    return (!!in_destroy(q->hwnd) == q->expected) ? 0 : 1;
}
static void query_other_thread(HWND hwnd, BOOL expected)
{
    struct query q = {hwnd, expected};
    HANDLE thread = CreateThread(NULL, 0, query_thread, &q, 0, NULL);
    DWORD code = 99;
    CHECK(thread != NULL);
    if (!thread) return;
    /* Query must be nonblocking: the HWND thread is waiting here. */
    CHECK(WaitForSingleObject(thread, 10000) == WAIT_OBJECT_0);
    CHECK(GetExitCodeThread(thread, &code) && code == 0);
    CloseHandle(thread);
}
static void query_other_process(HWND hwnd, BOOL expected)
{
    WCHAR executable[MAX_PATH], command[MAX_PATH + 80];
    STARTUPINFOW start = {0};
    PROCESS_INFORMATION process = {0};
    DWORD code = 99;
    start.cb = sizeof(start);
    CHECK(GetModuleFileNameW(NULL, executable, MAX_PATH));
    swprintf(command, sizeof(command)/sizeof(*command), L"\"%ls\" --query %llx %u",
             executable, (unsigned long long)(ULONG_PTR)hwnd, expected);
    CHECK(CreateProcessW(NULL, command, NULL, NULL, FALSE, 0, NULL, NULL, &start, &process));
    if (!process.hProcess) return;
    CHECK(WaitForSingleObject(process.hProcess, 15000) == WAIT_OBJECT_0);
    CHECK(GetExitCodeProcess(process.hProcess, &code) && code == 0);
    CloseHandle(process.hThread); CloseHandle(process.hProcess);
}
static LRESULT CALLBACK cbt_hook(int code, WPARAM wparam, LPARAM lparam)
{
    if (code == HCBT_DESTROYWND && (HWND)wparam == root)
    {
        ++hook_seen;
        CHECK(!in_destroy(root));
        if (veto) return 1;
    }
    return CallNextHookEx(NULL, code, wparam, lparam);
}
static LRESULT CALLBACK proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam)
{
    if (msg == WM_PARENTNOTIFY && LOWORD(wparam) == WM_DESTROY)
    {
        ++parent_notified;
        CHECK(!in_destroy((HWND)lparam));
    }
    if (msg == WM_DESTROY)
    {
        BOOL expected = !(recursive_child && hwnd == child);
        ++destroyed;
        CHECK(!!in_destroy(hwnd) == expected);
        CHECK(IsWindow(hwnd));
        query_other_thread(hwnd, expected);
        if (check_foreign) query_other_process(hwnd, expected);
        if (hwnd == root && recursive_child)
        {
            CHECK(!in_destroy(child));
            CHECK(in_destroy(root));
        }
    }
    if (msg == WM_NCDESTROY)
    {
        ++nc_destroyed;
        CHECK(in_destroy(hwnd));
        CHECK(IsWindow(hwnd));
        query_other_thread(hwnd, TRUE);
        if (check_foreign) query_other_process(hwnd, TRUE);
    }
    return DefWindowProcW(hwnd, msg, wparam, lparam);
}
static HWND create(HWND parent, DWORD style)
{
    HWND hwnd = CreateWindowW(L"GHUBWindowDestroy", L"", style, 0, 0, 20, 20,
                              parent, NULL, GetModuleHandleW(NULL), NULL);
    CHECK(hwnd != NULL);
    CHECK(!in_destroy(hwnd));
    return hwnd;
}
int wmain(int argc, WCHAR **argv)
{
    WNDCLASSW cls = {0};
    HHOOK hook;
    HWND old;
    unsigned i;
    in_destroy = (void *)GetProcAddress(GetModuleHandleW(L"user32.dll"), "IsWindowInDestroy");
    CHECK(in_destroy != NULL);
    if (!in_destroy) return 1;
    if (argc == 4 && !wcscmp(argv[1], L"--query"))
        return !!in_destroy((HWND)(ULONG_PTR)wcstoull(argv[2], NULL, 16)) != !!wcstoul(argv[3], NULL, 10);
    cls.lpfnWndProc = proc; cls.hInstance = GetModuleHandleW(NULL); cls.lpszClassName = L"GHUBWindowDestroy";
    CHECK(RegisterClassW(&cls));
    CHECK(!in_destroy(NULL));
    CHECK(!in_destroy((HWND)(ULONG_PTR)0xdeadbeef));
    CHECK(!in_destroy(GetDesktopWindow()));
    root = create(NULL, WS_POPUP);
    query_other_thread(root, FALSE);
    query_other_process(root, FALSE);
    hook = SetWindowsHookExW(WH_CBT, cbt_hook, NULL, GetCurrentThreadId());
    CHECK(hook != NULL);
    veto = TRUE;
    CHECK(!DestroyWindow(root));
    CHECK(IsWindow(root) && !in_destroy(root));
    CHECK(hook_seen == 1);
    veto = FALSE;
    child = create(root, WS_CHILD);
    recursive_child = check_foreign = TRUE;
    CHECK(DestroyWindow(root));
    CHECK(destroyed == 2 && nc_destroyed == 2);
    CHECK(!IsWindow(root) && !in_destroy(root));
    CHECK(!IsWindow(child) && !in_destroy(child));
    query_other_process(root, FALSE);
    CHECK(UnhookWindowsHookEx(hook));
    recursive_child = check_foreign = FALSE;
    root = create(NULL, WS_POPUP);
    child = create(root, WS_CHILD);
    CHECK(DestroyWindow(child));
    CHECK(parent_notified == 1);
    CHECK(!in_destroy(root));
    CHECK(DestroyWindow(root));
    old = root;
    for (i = 0; i < 32; ++i)
    {
        root = create(HWND_MESSAGE, 0);
        CHECK(!in_destroy(old));
        CHECK(DestroyWindow(root));
        CHECK(!in_destroy(root));
    }
    CHECK(UnregisterClassW(cls.lpszClassName, cls.hInstance));
    printf("Window destruction: %u checks, %u failures\n", checks, failures);
    return failures ? 1 : 0;
}
