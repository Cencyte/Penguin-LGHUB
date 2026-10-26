// Bounded, headless-lab debugger for the SHA-pinned dwmcorei overlay path.
// Breakpoints change only private process memory. No HRESULTs or data are patched.
#include <windows.h>
#include <shellapi.h>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <stdexcept>
#include <set>
#include <vector>
#include <map>
#include <string>

struct Point
{
    DWORD rva;
    const char *name;
    BYTE bytes[6];
    unsigned size;
    unsigned hits = 0;
    bool tray = false;
};
static Point points[] = {
    {0x887f8, "ensure_entry", {0x48, 0x89, 0x5c, 0x24, 0x08}, 5},
    {0x8888f, "linear_call", {0xe8, 0x7c, 0xe7, 0x13, 0x00}, 5},
    {0x88894, "linear_return", {0x8b, 0xd8, 0x85, 0xc0}, 4},
    {0x888ab, "border_return", {0x8b, 0xd8, 0x85, 0xc0}, 4},
    {0x888c5, "backface_return", {0x8b, 0xd8, 0x85, 0xc0}, 4},
    {0x88a69, "overlay_one_created", {0x85, 0xdb, 0x0f, 0x88}, 4},
    {0x88b20, "overlay_two_created", {0x85, 0xdb, 0x78, 0x3f}, 4},
    {0x88c87, "ensure_return", {0x4c, 0x8d, 0x5c, 0x24, 0x50}, 5},
    {0x82830, "target_begin", {0x48, 0x89, 0x5c, 0x24, 0x08}, 5},
    {0x82868, "overlay_begin", {0x48, 0x89, 0x5c, 0x24, 0x10}, 5},
    {0x899f8, "composition_error", {0x48, 0x89, 0x5c, 0x24, 0x08}, 5},
    {0x88c21, "tray_icon_add", {0xff, 0x15, 0x51, 0xf0, 0x65, 0x00}, 6, 0, true},
    {0x88c27, "tray_icon_add_return", {0x48, 0x8b, 0x84, 0x24, 0x90}, 5, 0, true},
    {0x886e0, "tray_subclass_entry", {0x4c, 0x89, 0x4c, 0x24, 0x20}, 5, 0, true},
};
static FILE *trace;
static HANDLE process;
static ULONG_PTR base;
static ULONG_PTR dcomp_base;
static ULONG_PTR tray_base;
static DWORD tray_pid;
static NOTIFYICONDATAW tray_icon = {};
static bool tray_icon_registered;
static std::map<ULONG_PTR, std::wstring> modules;

static void check(BOOL ok, const char *operation)
{
    if (!ok)
    {
        std::fprintf(stderr, "probe_error operation=%s winerror=%lu\n", operation, GetLastError());
        throw std::runtime_error(operation);
    }
}
static bool read(ULONG_PTR address, void *data, SIZE_T size)
{
    SIZE_T count = 0;
    return ReadProcessMemory(process, reinterpret_cast<void *>(address), data, size, &count) && count == size;
}
static bool mapped_address(ULONG_PTR address)
{
    MEMORY_BASIC_INFORMATION mapping = {};
    if (!VirtualQueryEx(process, reinterpret_cast<void *>(address), &mapping, sizeof(mapping))
            || mapping.Type != MEM_IMAGE) return false;
    ULONG_PTR image = reinterpret_cast<ULONG_PTR>(mapping.AllocationBase);
    auto it = modules.find(image);
    std::fprintf(trace, " module=%ls base=%llx rva=%llx", it == modules.end() ? L"unknown" : it->second.c_str(),
                 static_cast<unsigned long long>(image), static_cast<unsigned long long>(address - image));
    return true;
}
static void report_exception(const EXCEPTION_DEBUG_INFO &info, DWORD tid, const CONTEXT &context)
{
    const auto &record = info.ExceptionRecord;
    std::fprintf(trace, "overlay_trace exception code=%08lx first=%lu tid=%lx flags=%lx rip=%llx rsp=%llx",
                 record.ExceptionCode, info.dwFirstChance, tid, record.ExceptionFlags, context.Rip, context.Rsp);
    mapped_address(context.Rip);
    for (DWORD i = 0; i < record.NumberParameters && i < EXCEPTION_MAXIMUM_PARAMETERS; ++i)
        std::fprintf(trace, " param%lu=%llx", i, static_cast<unsigned long long>(record.ExceptionInformation[i]));
    DWORD repeated[2] = {};
    if (base && read(base + 0x28c488, repeated, sizeof(repeated)))
        std::fprintf(trace, " repeated_hresult=%08lx failure_score=%lu", repeated[0], repeated[1]);
    std::fputc('\n', trace);
    // Raw stack candidates, NOT an unwound backtrace. Retain stack positions and
    // image RVAs so the saved corpus can distinguish return sites from data.
    for (unsigned i = 0; i < 96; ++i)
    {
        ULONG_PTR value;
        if (!read(context.Rsp + i * sizeof(value), &value, sizeof(value))) break;
        std::fprintf(trace, "overlay_trace stack_candidate offset=%x value=%llx", i * 8,
                     static_cast<unsigned long long>(value));
        mapped_address(value);
        std::fputc('\n', trace);
    }
}
static void patch(Point &point, BYTE byte)
{
    void *address = reinterpret_cast<void *>((point.tray ? tray_base : base) + point.rva);
    DWORD protection, ignored;
    SIZE_T count = 0;
    check(VirtualProtectEx(process, address, 1, PAGE_EXECUTE_READWRITE, &protection), "protect breakpoint");
    BOOL written = WriteProcessMemory(process, address, &byte, 1, &count);
    check(VirtualProtectEx(process, address, 1, protection, &ignored), "restore protection");
    check(written && count == 1, "write breakpoint");
    check(FlushInstructionCache(process, address, 1), "flush breakpoint");
}
static void object(ULONG_PTR address)
{
    std::fprintf(trace, " object=%llx", static_cast<unsigned long long>(address));
    for (unsigned offset : {0xb8u, 0xc0u, 0xd0u, 0xd8u, 0xe0u, 0xe8u, 0x108u, 0x110u})
    {
        ULONG_PTR value;
        if (address && read(address + offset, &value, sizeof(value)))
            std::fprintf(trace, " field_%x=%llx", offset, static_cast<unsigned long long>(value));
        else
            std::fprintf(trace, " field_%x=unreadable", offset);
    }
}
static void report(Point &p, DWORD tid, const CONTEXT &c)
{
    ++p.hits;
    if (p.tray)
    {
        if (p.rva == 0x886e0)
        {
            if (static_cast<UINT>(c.Rdx) == 0x500)
                std::fprintf(trace, "tray_probe native_callback hwnd=%llx msg=%llx wparam=%llx lparam=%llx\n",
                             c.Rcx, c.Rdx, c.R8, c.R9);
            return;
        }
        if (p.rva == 0x88c21)
        {
            check(c.Rcx == NIM_ADD && read(c.Rdx, &tray_icon, sizeof(tray_icon)), "read native tray registration");
            check(tray_icon.cbSize == sizeof(tray_icon) && (tray_icon.uFlags & NIF_MESSAGE), "tray notification ABI");
            std::fprintf(trace, "tray_probe icon_add tid=%lx hwnd=%p id=%u flags=%x callback=%x\n",
                         tid, tray_icon.hWnd, tray_icon.uID, tray_icon.uFlags, tray_icon.uCallbackMessage);
        }
        else
        {
            tray_icon_registered = static_cast<DWORD>(c.Rax) != 0;
            std::fprintf(trace, "tray_probe icon_add_return success=%d\n", tray_icon_registered);
        }
        return;
    }
    if (p.rva == 0x899f8)
    {
        ULONG_PTR caller = 0;
        DWORD line = 0;
        read(c.Rsp, &caller, sizeof(caller));
        read(c.Rsp + 0x28, &line, sizeof(line));
        std::fprintf(trace, "overlay_trace event=composition_error tid=%lx flags=%lx hresult=%08lx line=%lu caller=%llx",
                     tid, static_cast<DWORD>(c.Rcx), static_cast<DWORD>(c.R9), line,
                     static_cast<unsigned long long>(caller));
        mapped_address(caller);
        std::fputc('\n', trace);
        return;
    }
    std::fprintf(trace, "overlay_trace event=%s tid=%lx rva=%lx rcx=%llx rdi=%llx eax=%08lx ebx=%08lx",
                p.name, tid, p.rva, c.Rcx, c.Rdi, static_cast<DWORD>(c.Rax), static_cast<DWORD>(c.Rbx));
    if (p.rva == 0x887f8)
        object(c.Rcx);
    else if (p.rva == 0x82830)
        object(c.Rcx >= 0xb0 ? c.Rcx - 0xb0 : 0);
    else if (p.rva != 0x82868)
        object(c.Rdi);
    if (p.rva == 0x8888f)
    {
        ULONG_PTR table = 0, slot = 0;
        MEMORY_BASIC_INFORMATION mapping = {};
        bool have_slot = read(c.Rcx, &table, sizeof(table)) && read(table + 0x58, &slot, sizeof(slot));
        bool have_module = VirtualQueryEx(process, reinterpret_cast<void *>(c.Rax), &mapping, sizeof(mapping))
                           && mapping.Type == MEM_IMAGE;
        ULONG_PTR module = have_module ? reinterpret_cast<ULONG_PTR>(mapping.AllocationBase) : 0;
        std::fprintf(trace, " receiver=%llx mode=%llx target=%llx vtable=%llx slot_58=%llx slot_read=%d"
                     " target_module=%s target_base=%llx target_rva=%llx", c.Rcx, c.Rdx, c.Rax,
                     static_cast<unsigned long long>(table), static_cast<unsigned long long>(slot), have_slot,
                     module && module == dcomp_base ? "dcomp.dll" : "unknown",
                     static_cast<unsigned long long>(module), module ? c.Rax - module : 0);
    }
    std::fputc('\n', trace);
}
static BOOL CALLBACK inventory_window(HWND hwnd, LPARAM children)
{
    DWORD pid = 0;
    DWORD tid = GetWindowThreadProcessId(hwnd, &pid);
    if (pid != tray_pid) return TRUE;
    wchar_t name[256] = {}, title[256] = {};
    RECT rect = {}, client = {};
    GetClassNameW(hwnd, name, 256);
    GetWindowTextW(hwnd, title, 256);
    GetWindowRect(hwnd, &rect);
    GetClientRect(hwnd, &client);
    std::fprintf(trace, "tray_probe window hwnd=%p parent=%p tid=%lx visible=%d class=%ls title=%ls "
                       "style=%08lx rect=%ld,%ld,%ld,%ld client=%ld,%ld\n",
                 hwnd, GetParent(hwnd), tid, IsWindowVisible(hwnd), name, title,
                 static_cast<DWORD>(GetWindowLongW(hwnd, GWL_STYLE)),
                 rect.left, rect.top, rect.right, rect.bottom, client.right, client.bottom);
    if (children) EnumChildWindows(hwnd, inventory_window, 0);
    return TRUE;
}

int wmain(int argc, wchar_t **argv)
{
    if (argc != 3 && (argc != 4 || (wcscmp(argv[3], L"--exercise-tray") &&
                                                  wcscmp(argv[3], L"--exercise-resize") &&
                                                  wcscmp(argv[3], L"--exercise-pointer"))))
    {
        std::fprintf(stderr, "usage: tray_overlay_trace.exe absolute-tray.exe trace-log [--exercise-tray|--exercise-resize]\n");
        return 2;
    }
    bool exercise = argc == 4;
    bool resize = exercise && !wcscmp(argv[3], L"--exercise-resize");
    bool pointer = exercise && !wcscmp(argv[3], L"--exercise-pointer");
    wchar_t private_lab[2] = {};
    if (exercise && (GetEnvironmentVariableW(L"GHUB_TRAY_PRIVATE_LAB", private_lab, 2) != 1 || private_lab[0] != L'1'))
        return 2;
    trace = _wfopen(argv[2], L"w");
    if (!trace) return 2;
    setvbuf(trace, nullptr, _IONBF, 0);
    STARTUPINFOW startup = {};
    startup.cb = sizeof(startup);
    // Preserve native DLL diagnostics as well as Wine's Unix-side trace. A
    // debuggee created without inherited std handles loses its CRT stderr.
    HANDLE standard[3] = {};
    const DWORD ids[] = {STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, STD_ERROR_HANDLE};
    for (unsigned i = 0; i < 3; ++i)
        if (!DuplicateHandle(GetCurrentProcess(), GetStdHandle(ids[i]), GetCurrentProcess(), &standard[i],
                             0, TRUE, DUPLICATE_SAME_ACCESS))
        {
            std::fprintf(stderr, "Duplicate standard handle failed: %lu\n", GetLastError());
            for (HANDLE handle : standard) if (handle) CloseHandle(handle);
            std::fclose(trace);
            return 1;
        }
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = standard[0];
    startup.hStdOutput = standard[1];
    startup.hStdError = standard[2];
    PROCESS_INFORMATION child = {};
    BOOL created = CreateProcessW(argv[1], nullptr, nullptr, nullptr, TRUE, DEBUG_ONLY_THIS_PROCESS,
                                  nullptr, nullptr, &startup, &child);
    DWORD create_error = GetLastError();
    for (HANDLE handle : standard) CloseHandle(handle);
    if (!created)
    {
        std::fprintf(stderr, "CreateProcess failed: %lu\n", create_error);
        std::fclose(trace);
        return 1;
    }
    process = child.hProcess;
    tray_pid = child.dwProcessId;
    CloseHandle(child.hThread);
    std::set<DWORD> threads;
    std::vector<HANDLE> suspended;
    Point *stepping = nullptr;
    DWORD stepping_tid = 0;
    bool initial_break = false, exited = false;
    unsigned faults = 0;
    const ULONGLONG started = GetTickCount64(), deadline = started + 60000;
    ULONGLONG next_inventory = started + 10000;
    bool exercised = false;
    unsigned resize_steps = 0;
    unsigned pointer_steps = 0;
    ULONGLONG next_pointer = started + 22000;
    RECT original_rect = {};
    int result = 0;
    try
    {
        check(DebugSetProcessKillOnExit(TRUE), "kill debuggee on exit");
        while (GetTickCount64() < deadline && !exited)
        {
            // Inspection and queued shell notifications occur only between debug
            // events, never while this debugger deliberately holds peer threads.
            if (exercise && !stepping && GetTickCount64() >= next_inventory)
            {
                std::fprintf(trace, "tray_probe inventory elapsed_ms=%llu\n", GetTickCount64() - started);
                EnumWindows(inventory_window, 1);
                next_inventory = GetTickCount64() + 5000;
                if (!exercised && tray_icon_registered && GetTickCount64() - started >= 15000)
                {
                    DWORD owner = 0;
                    check(GetWindowThreadProcessId(tray_icon.hWnd, &owner) && owner == tray_pid,
                          "notification target belongs to owned tray process");
                    // This pinned binary uses the original notification ABI and
                    // never NIM_SETVERSION. Emulate the shell's right-button pair,
                    // not a window command or a patched application return value.
                    if (pointer) check(SetCursorPos(740, 730), "place private activation pointer");
                    check(PostMessageW(tray_icon.hWnd, tray_icon.uCallbackMessage, tray_icon.uID, WM_RBUTTONDOWN),
                          "queue private tray right-button down");
                    check(PostMessageW(tray_icon.hWnd, tray_icon.uCallbackMessage, tray_icon.uID, WM_RBUTTONUP),
                          "queue private tray right-button up");
                    std::fprintf(trace, "tray_probe queued_shell_right_click hwnd=%p id=%u callback=%x\n",
                                 tray_icon.hWnd, tray_icon.uID, tray_icon.uCallbackMessage);
                    exercised = true;
                }
                if (resize && exercised && resize_steps < 2 && IsWindowVisible(tray_icon.hWnd) &&
                    GetTickCount64() - started >= (resize_steps ? 40000 : 30000))
                {
                    DWORD owner = 0;
                    check(GetWindowThreadProcessId(tray_icon.hWnd, &owner) && owner == tray_pid,
                          "resize target belongs to owned tray process");
                    if (!resize_steps) check(GetWindowRect(tray_icon.hWnd, &original_rect), "read original tray size");
                    const int width = original_rect.right - original_rect.left + (resize_steps ? 0 : 64);
                    const int height = original_rect.bottom - original_rect.top + (resize_steps ? 0 : 32);
                    // Only the newly launched private-display process is touched.
                    // Queue the normal window-size operation; no debuggee data
                    // or return value is patched to simulate a successful resize.
                    check(SetWindowPos(tray_icon.hWnd, nullptr, 0, 0, width, height,
                                       SWP_ASYNCWINDOWPOS | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE),
                          "queue private tray window resize");
                    std::fprintf(trace, "tray_probe queued_window_resize step=%u hwnd=%p size=%dx%d\n",
                                 resize_steps, tray_icon.hWnd, width, height);
                    ++resize_steps;
                }
            }
            if (pointer && exercised && !stepping && pointer_steps < 12 && GetTickCount64() >= next_pointer)
            {
                RECT rect;
                DWORD owner = 0;
                check(GetWindowThreadProcessId(tray_icon.hWnd, &owner) && owner == tray_pid,
                      "pointer target belongs to owned process");
                check(GetWindowRect(tray_icon.hWnd, &rect) && IsWindowVisible(tray_icon.hWnd), "visible pointer target");
                if (GetEnvironmentVariableW(L"GHUB_EXPECT_TRAY_ANCHOR", nullptr, 0))
                {
                    check(rect.left >= 0 && rect.right <= 1024 && rect.top >= 0 && rect.bottom <= 768,
                          "popup is inside the owned monitor");
                    check(rect.left <= 740 && rect.right >= 740 && rect.bottom >= 720 && rect.bottom <= 734,
                          "popup is adjacent to the activation pointer");
                }
                const int x = rect.left + (rect.right-rect.left) * (1+pointer_steps%3) / 4;
                const int y = rect.top + (rect.bottom-rect.top) * (1+pointer_steps%6) / 7;
                check(SetCursorPos(x, y), "move private pointer over native popup");
                std::fprintf(trace, "tray_probe pointer step=%u xy=%d,%d rect=%ld,%ld,%ld,%ld\n",
                             pointer_steps, x, y, rect.left, rect.top, rect.right, rect.bottom);
                ++pointer_steps;
                next_pointer = GetTickCount64() + 750;
            }
            DEBUG_EVENT event = {};
            if (!WaitForDebugEvent(&event, 1000))
            {
                if (GetLastError() == ERROR_SEM_TIMEOUT) continue;
                check(FALSE, "wait debug event");
            }
            DWORD status = DBG_CONTINUE;
            if (event.dwDebugEventCode == CREATE_PROCESS_DEBUG_EVENT)
            {
                threads.insert(event.dwThreadId);
                tray_base = reinterpret_cast<ULONG_PTR>(event.u.CreateProcessInfo.lpBaseOfImage);
                modules[tray_base] = L"lghub_system_tray.exe";
                for (auto &p : points) if (p.tray)
                {
                    BYTE bytes[6];
                    check(read(tray_base + p.rva, bytes, p.size) && !memcmp(bytes, p.bytes, p.size), "tray instruction bytes");
                }
                for (auto &p : points) if (p.tray) patch(p, 0xcc);
                if (event.u.CreateProcessInfo.hFile) CloseHandle(event.u.CreateProcessInfo.hFile);
            }
            else if (event.dwDebugEventCode == CREATE_THREAD_DEBUG_EVENT)
                threads.insert(event.dwThreadId);
            else if (event.dwDebugEventCode == EXIT_THREAD_DEBUG_EVENT)
                threads.erase(event.dwThreadId);
            else if (event.dwDebugEventCode == LOAD_DLL_DEBUG_EVENT)
            {
                wchar_t path[2048] = {};
                HANDLE file = event.u.LoadDll.hFile;
                DWORD length = file ? GetFinalPathNameByHandleW(file, path, 2048, FILE_NAME_NORMALIZED) : 0;
                if (file) CloseHandle(file);
                const wchar_t *name = wcsrchr(path, L'\\');
                if (length && length < 2048 && name)
                    modules[reinterpret_cast<ULONG_PTR>(event.u.LoadDll.lpBaseOfDll)] = name + 1;
                if (length && length < 2048 && name && !_wcsicmp(name + 1, L"dcomp.dll"))
                    dcomp_base = reinterpret_cast<ULONG_PTR>(event.u.LoadDll.lpBaseOfDll);
                if (length && length < 2048 && name && !_wcsicmp(name + 1, L"dwmcorei.dll"))
                {
                    check(!base, "duplicate dwmcorei module");
                    base = reinterpret_cast<ULONG_PTR>(event.u.LoadDll.lpBaseOfDll);
                    // Validate every location before installing any breakpoint.
                    for (auto &p : points) if (!p.tray)
                    {
                        BYTE bytes[6];
                        check(read(base + p.rva, bytes, p.size) && !memcmp(bytes, p.bytes, p.size), "expected instruction bytes");
                    }
                    for (auto &p : points) if (!p.tray) patch(p, 0xcc);
                    std::fprintf(trace, "overlay_trace armed base=%llx pid=%lx points=%zu\n",
                                static_cast<unsigned long long>(base), event.dwProcessId, sizeof(points) / sizeof(points[0]));
                }
            }
            else if (event.dwDebugEventCode == UNLOAD_DLL_DEBUG_EVENT)
                modules.erase(reinterpret_cast<ULONG_PTR>(event.u.UnloadDll.lpBaseOfDll));
            else if (event.dwDebugEventCode == EXCEPTION_DEBUG_EVENT)
            {
                const auto &exception = event.u.Exception.ExceptionRecord;
                ULONG_PTR address = reinterpret_cast<ULONG_PTR>(exception.ExceptionAddress);
                Point *hit = nullptr;
                for (auto &p : points)
                {
                    ULONG_PTR module = p.tray ? tray_base : base;
                    if (module && address == module + p.rva && exception.ExceptionCode == EXCEPTION_BREAKPOINT) hit = &p;
                }
                HANDLE thread = OpenThread(THREAD_GET_CONTEXT | THREAD_SET_CONTEXT, FALSE, event.dwThreadId);
                check(thread != nullptr, "open event thread");
                CONTEXT context = {};
                context.ContextFlags = CONTEXT_FULL;
                BOOL got_context = GetThreadContext(thread, &context);
                if (!got_context) CloseHandle(thread);
                check(got_context, "read event context");
                if (hit)
                {
                    check(!stepping, "non-overlapping single step");
                    report(*hit, event.dwThreadId, context);
                    // Hold other threads while the original instruction is exposed.
                    for (DWORD tid : threads)
                    {
                        if (tid == event.dwThreadId) continue;
                        HANDLE other = OpenThread(THREAD_SUSPEND_RESUME | SYNCHRONIZE, FALSE, tid);
                        check(other != nullptr, "open peer thread");
                        if (SuspendThread(other) == static_cast<DWORD>(-1))
                        {
                            DWORD error = GetLastError();
                            // Wine reports ACCESS_DENIED for a terminated thread.
                            // Its EXIT_THREAD event can still be pending. Only
                            // skip peers proven unable to execute instructions.
                            bool terminated = WaitForSingleObject(other, 0) == WAIT_OBJECT_0;
                            CloseHandle(other);
                            if (terminated)
                            {
                                std::fprintf(trace, "overlay_trace terminated_peer tid=%lx suspend_error=%lu\n", tid, error);
                                continue;
                            }
                            SetLastError(error);
                            check(FALSE, "suspend peer thread");
                        }
                        suspended.push_back(other);
                    }
                    patch(*hit, hit->bytes[0]);
                    context.Rip = address;
                    context.EFlags |= 0x100;
                    check(SetThreadContext(thread, &context), "step original instruction");
                    stepping = hit;
                    stepping_tid = event.dwThreadId;
                }
                else if (exception.ExceptionCode == EXCEPTION_SINGLE_STEP && stepping && stepping_tid == event.dwThreadId)
                {
                    patch(*stepping, 0xcc);
                    context.EFlags &= ~0x100;
                    check(SetThreadContext(thread, &context), "clear trap flag");
                    stepping = nullptr;
                    for (HANDLE other : suspended)
                    {
                        check(ResumeThread(other) != static_cast<DWORD>(-1), "resume peer thread");
                        CloseHandle(other);
                    }
                    suspended.clear();
                }
                else if (exception.ExceptionCode == EXCEPTION_BREAKPOINT && !initial_break)
                    initial_break = true;
                else
                {
                    if (exception.ExceptionCode == 0xe0434e49 || exception.ExceptionCode == 0xc0000602
                            || !event.u.Exception.dwFirstChance)
                        report_exception(event.u.Exception, event.dwThreadId, context);
                    if (exception.ExceptionCode == EXCEPTION_ACCESS_VIOLATION)
                    {
                        ++faults;
                        std::fprintf(trace, "overlay_trace access_violation tid=%lx first=%lu rip=%llx dwm_rva=%llx rcx=%llx address=%llx\n",
                                    event.dwThreadId, event.u.Exception.dwFirstChance, context.Rip,
                                    base ? context.Rip - base : 0, context.Rcx,
                                    static_cast<unsigned long long>(exception.ExceptionInformation[1]));
                    }
                    status = DBG_EXCEPTION_NOT_HANDLED;
                }
                CloseHandle(thread);
            }
            else if (event.dwDebugEventCode == EXIT_PROCESS_DEBUG_EVENT)
            {
                std::fprintf(trace, "overlay_trace exit code=%lx\n", event.u.ExitProcess.dwExitCode);
                exited = true;
            }
            check(ContinueDebugEvent(event.dwProcessId, event.dwThreadId, status), "continue debug event");
        }
        std::fprintf(trace, "overlay_trace summary armed=%d exited=%d access_violations=%u\n", base != 0, exited, faults);
        for (auto &p : points) std::fprintf(trace, "overlay_trace count event=%s hits=%u\n", p.name, p.hits);
        if (exercise) std::fprintf(trace, "tray_probe summary registered=%d exercised=%d\n", tray_icon_registered, exercised);
        if (resize) std::fprintf(trace, "tray_probe resize_summary queued=%u\n", resize_steps);
        if (!base || !points[0].hits) result = 3;
        if (exercise && !exercised) result = 4;
        if (resize && resize_steps != 2) result = 5;
        if (pointer && (pointer_steps != 12 || exited || faults)) result = 6;
    }
    catch (const std::exception &) { result = 1; }
    if (!exited) TerminateProcess(process, 0xdead);
    for (HANDLE other : suspended) CloseHandle(other);
    CloseHandle(process);
    std::fclose(trace);
    return result;
}
