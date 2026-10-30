/* Synthetic G HUB SendInput worker patterns. Run only through the private
 * host_keyboard_threads.py harness; never target an interactive desktop.
 * Build: x86_64-w64-mingw32-gcc -O2 -static host_keyboard_threads.c -o lghub_agent.exe
 */
#include <windows.h>
#include <stdio.h>

struct worker { HANDLE go, done, thread; INPUT input; BOOL stop, cleanup; };
static LONG failures;

static DWORD WINAPI run_worker(void *arg)
{
    struct worker *w = arg;
    for (;;)
    {
        WaitForSingleObject(w->go, INFINITE);
        if (w->stop) break;
        ULONGLONG now = GetTickCount64();
        UINT sent = SendInput(1, &w->input, sizeof(w->input));
        printf("SEND %llu %lu %x %x %lx %u %d\n", now, GetCurrentThreadId(),
               w->input.ki.wVk, w->input.ki.wScan, w->input.ki.dwFlags, sent, w->cleanup);
        fflush(stdout);
        if (sent != 1) InterlockedIncrement(&failures);
        SetEvent(w->done);
    }
    return 0;
}

static void start(struct worker *w)
{
    ZeroMemory(w, sizeof(*w));
    w->go = CreateEventW(NULL, FALSE, FALSE, NULL);
    w->done = CreateEventW(NULL, FALSE, FALSE, NULL);
    w->thread = CreateThread(NULL, 0, run_worker, w, 0, NULL);
    if (!w->go || !w->done || !w->thread) ExitProcess(2);
}

static void stop(struct worker *w)
{
    w->stop = TRUE; SetEvent(w->go);
    if (WaitForSingleObject(w->thread, 5000) != WAIT_OBJECT_0) ExitProcess(2);
    CloseHandle(w->go); CloseHandle(w->done); CloseHandle(w->thread);
}

static void key(struct worker *w, WORD vk, WORD scan, BOOL up, BOOL cleanup)
{
    ZeroMemory(&w->input, sizeof(w->input));
    w->input.type = INPUT_KEYBOARD; w->input.ki.wVk = vk; w->input.ki.wScan = scan;
    w->input.ki.dwFlags = up ? KEYEVENTF_KEYUP : 0; w->cleanup = cleanup;
    SetEvent(w->go);
    if (WaitForSingleObject(w->done, 5000) != WAIT_OBJECT_0) ExitProcess(2);
}

static void mark(const char *name)
{
    printf("CASE %s %llu\n", name, GetTickCount64()); fflush(stdout);
}

int main(void)
{
    struct worker a, b;
    if (!GetEnvironmentVariableA("GHUB_HOST_INPUT_PRIVATE_TEST", NULL, 0)) return 2;
    start(&a); start(&b);
    /* Warm the emulation connections separately; cold startup is not timed. */
    key(&a, VK_LSHIFT, 0x2a, FALSE, FALSE); key(&a, VK_LSHIFT, 0x2a, TRUE, FALSE); Sleep(400);
    key(&b, VK_LSHIFT, 0x2a, FALSE, FALSE); key(&b, VK_LSHIFT, 0x2a, TRUE, FALSE); Sleep(400);
    for (int mode = 0; mode < 3; ++mode)
    {
        struct worker *press = mode == 2 ? &b : &a;
        struct worker *release = mode == 1 ? &b : &a;
        mark(mode == 0 ? "same-thread" : mode == 1 ? "cross-thread" : "reverse-thread");
        for (int i = 0; i < 3; ++i)
        {
            key(press, VK_OEM_4, 0x1a, FALSE, FALSE); Sleep(80);
            key(release, VK_OEM_4, 0x1a, TRUE, FALSE); Sleep(700);
            if (mode) { key(press, VK_OEM_4, 0x1a, TRUE, TRUE); Sleep(100); }
        }
    }
    mark("rapid-taps");
    for (int i = 0; i < 24; ++i)
    {
        key(&a, VK_OEM_4, 0x1a, FALSE, FALSE); Sleep(15);
        key(&b, VK_OEM_4, 0x1a, TRUE, FALSE); Sleep(15);
    }
    Sleep(700); key(&a, VK_OEM_4, 0x1a, TRUE, TRUE); Sleep(100);
    mark("modifier-chord");
    key(&a, VK_LCONTROL, 0x1d, FALSE, FALSE);
    key(&b, 'A', 0x1e, FALSE, FALSE); Sleep(80);
    key(&a, 'A', 0x1e, TRUE, FALSE); key(&b, VK_LCONTROL, 0x1d, TRUE, FALSE);
    Sleep(700); key(&b, 'A', 0x1e, TRUE, TRUE); key(&a, VK_LCONTROL, 0x1d, TRUE, TRUE);
    Sleep(100);
    mark("worker-exit");
    key(&a, VK_OEM_4, 0x1a, FALSE, FALSE); stop(&a); Sleep(80);
    key(&b, VK_OEM_4, 0x1a, TRUE, FALSE); Sleep(700);
    start(&a);
    mark("intentional-hold");
    key(&a, VK_OEM_4, 0x1a, FALSE, FALSE); Sleep(850);
    key(&b, VK_OEM_4, 0x1a, TRUE, FALSE); Sleep(700);
    key(&a, VK_OEM_4, 0x1a, TRUE, TRUE); Sleep(100);
    mark("end"); stop(&a); stop(&b);
    return failures ? 1 : 0;
}
