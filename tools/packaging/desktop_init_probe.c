/* Fault injection ONLY inside the diagnostic's disposable Wine desktop. */
#include <windows.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv)
{
    char guard[4], path[MAX_PATH], command[MAX_PATH + 32];
    DWORD desktop_pid, tid, started, result, code;
    HANDLE thread;
    STARTUPINFOA si = {sizeof(si)};
    PROCESS_INFORMATION pi;
    HDC dc;
    if (GetEnvironmentVariableA("PENGUIN_ISOLATED_UI_PROBE", guard, 4) != 1 || guard[0] != '1') return 2;
    dc = GetDC(NULL);
    if (!dc || !GetDeviceCaps(dc, HORZRES)) return 3;
    ReleaseDC(NULL, dc);
    if (argc == 2 && !strcmp(argv[1], "child")) return 0;
    if (argc != 1) return 2;
    tid = GetWindowThreadProcessId(GetDesktopWindow(), &desktop_pid);
    if (!tid || desktop_pid == GetCurrentProcessId()) return 4;
    thread = OpenThread(THREAD_SUSPEND_RESUME, FALSE, tid);
    if (!thread) return 5;
    GetModuleFileNameA(NULL, path, MAX_PATH);
    snprintf(command, sizeof(command), "\"%s\" child", path);
    if (SuspendThread(thread) == (DWORD)-1) { CloseHandle(thread); return 6; }
    started = GetTickCount();
    if (!CreateProcessA(NULL, command, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, NULL, &si, &pi))
    {
        ResumeThread(thread); CloseHandle(thread); return 7;
    }
    result = WaitForSingleObject(pi.hProcess, 8000);
    printf("DESKTOP_INIT before_resume=%d elapsed_ms=%lu\n", result == WAIT_OBJECT_0, GetTickCount()-started);
    ResumeThread(thread);
    CloseHandle(thread);
    if (WaitForSingleObject(pi.hProcess, 15000) != WAIT_OBJECT_0) TerminateProcess(pi.hProcess, 8);
    GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    return code;
}
