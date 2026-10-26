// Real native menu interaction on an explicitly owned, hardware-isolated display.
// The driver captures each CHECKPOINT before acknowledging it on our stdin pipe.
#include <windows.h>
#include <tlhelp32.h>
#include <cstdio>
#include <cwchar>
#include <cstring>
#include <initializer_list>
#include <vector>
#include <deque>

static DWORD tray_pid;
static HWND menu;
static unsigned outside_down, outside_up;
static int checks, failures;
static bool x11_input;
static bool plasma_tray;
static bool navigation;
static std::vector<RECT> monitors;
static CRITICAL_SECTION reply_lock;
static HANDLE reply_event;
static std::deque<int> replies;
static DWORD WINAPI read_replies(void *)
{
    for (;;)
    {
        int value = getchar();
        EnterCriticalSection(&reply_lock);
        replies.push_back(value);
        SetEvent(reply_event);
        LeaveCriticalSection(&reply_lock);
        if (value == EOF) return 0;
    }
}
static int reply()
{
    // This thread owns real windows. Blocking it on stdin delays cross-process
    // activation messages and can dismiss a freshly opened native popup.
    for (;;)
    {
        EnterCriticalSection(&reply_lock);
        if (!replies.empty())
        {
            int value = replies.front(); replies.pop_front();
            if (replies.empty()) ResetEvent(reply_event);
            LeaveCriticalSection(&reply_lock);
            return value;
        }
        LeaveCriticalSection(&reply_lock);
        if (MsgWaitForMultipleObjects(1,&reply_event,FALSE,INFINITE,QS_ALLINPUT) == WAIT_FAILED)
            return EOF;
        MSG message;
        while (PeekMessageW(&message,nullptr,0,0,PM_REMOVE))
        { TranslateMessage(&message); DispatchMessageW(&message); }
    }
}
static BOOL CALLBACK describe_monitor(HMONITOR monitor, HDC, LPRECT, LPARAM)
{
    MONITORINFO info = {}; info.cbSize = sizeof(info);
    if (!GetMonitorInfoW(monitor,&info)) return FALSE;
    monitors.push_back(info.rcMonitor);
    std::printf("MONITOR %ld %ld %ld %ld primary=%d\n", info.rcMonitor.left, info.rcMonitor.top,
        info.rcMonitor.right, info.rcMonitor.bottom, !!(info.dwFlags & MONITORINFOF_PRIMARY));
    return TRUE;
}
static COLORREF background_color = RGB(20,80,160);
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; std::printf("FAIL line=%d: %s error=%lu\n", __LINE__, #x, GetLastError()); } } while (0)
static void pump(DWORD ms)
{
    ULONGLONG end = GetTickCount64()+ms;
    do
    {
        MSG message;
        while (PeekMessageW(&message,nullptr,0,0,PM_REMOVE))
        { TranslateMessage(&message); DispatchMessageW(&message); }
        Sleep(10);
    } while (GetTickCount64()<end);
}

static BOOL CALLBACK find_menu(HWND hwnd, LPARAM)
{
    DWORD pid;
    wchar_t klass[256] = {};
    GetWindowThreadProcessId(hwnd, &pid);
    if (pid != tray_pid) return TRUE;
    GetClassNameW(hwnd, klass, 256);
    if (!wcscmp(klass, L"WinUIDesktopWin32WindowClass")) menu = hwnd;
    return TRUE;
}
static bool wait_visible(bool value)
{
    for (int i = 0; i < 50; ++i)
    {
        if (!!IsWindowVisible(menu) == value) return true;
        pump(100);
    }
    return false;
}
static void checkpoint(const char *name)
{
    pump(500);
    std::printf("CHECKPOINT %s\n", name);
    std::fflush(stdout);
    if (reply() == EOF) ExitProcess(2);
}
static void sample(int x, int y, COLORREF color)
{
    std::printf("CAPTURE %d %d %u %u %u\n",x,y,GetRValue(color),GetGValue(color),GetBValue(color));
    CHECK(reply() == '\n');
}
static bool wait_content()
{
    for (int i = 0; i < 20; ++i)
    {
        RECT rect = {}; GetWindowRect(menu,&rect);
        std::printf("CONTENT %ld %ld %ld %ld\n",rect.left,rect.top,
                    rect.right-rect.left,rect.bottom-rect.top);
        int response = reply();
        if (response == '\n') return true;
        if (response == EOF) return false;
        pump(250);
    }
    return false;
}
static bool open_menu()
{
    if (!plasma_tray) return !!PostMessageW(menu,0x500,0,WM_RBUTTONUP);
    POINT point = {}; GetCursorPos(&point);
    std::printf("SNI_OPEN %ld %ld\n",point.x,point.y);
    return reply() == '\n';
}
static BOOL CALLBACK find_frontend(HWND window, LPARAM value)
{
    wchar_t klass[128] = {}, title[128] = {};
    GetClassNameW(window,klass,128); GetWindowTextW(window,title,128);
    if (IsWindowVisible(window) && !wcscmp(klass,L"Chrome_WidgetWin_1") &&
        !wcscmp(title,L"Logitech G HUB")) *reinterpret_cast<HWND *>(value) = window;
    return TRUE;
}
static HWND frontend_window()
{
    HWND found = nullptr; EnumWindows(find_frontend,reinterpret_cast<LPARAM>(&found)); return found;
}
static void CALLBACK focus_event(HWINEVENTHOOK, DWORD event, HWND window, LONG object, LONG child,
                                 DWORD, DWORD timestamp)
{
    if (event != EVENT_SYSTEM_FOREGROUND && (window != menu || object != OBJID_WINDOW || child)) return;
    DWORD pid = 0; wchar_t klass[128] = {}, title[128] = {};
    GetWindowThreadProcessId(window,&pid);
    GetClassNameW(window,klass,128); GetWindowTextW(window,title,128);
    std::printf("FOCUS_EVENT time=%lu event=%lx window=%p pid=%lu class=%ls title=%ls visible=%d\n",
                timestamp,event,window,pid,klass,title,IsWindowVisible(window));
}
static bool wait_frontend(bool visible)
{
    for (int i=0;i<100;++i)
    {
        if (!!frontend_window() == visible) return true;
        pump(100);
    }
    return false;
}
static BOOL CALLBACK describe(HWND window, LPARAM)
{
    wchar_t klass[160] = {}; RECT rect = {};
    GetClassNameW(window,klass,160); GetWindowRect(window,&rect);
    std::printf("window %p class=%ls rect=%ld,%ld,%ld,%ld style=%lx exstyle=%lx cursor=%p\n",
        window,klass,rect.left,rect.top,rect.right,rect.bottom,GetWindowLongW(window,GWL_STYLE),
        GetWindowLongW(window,GWL_EXSTYLE),reinterpret_cast<void *>(GetClassLongPtrW(window,GCLP_HCURSOR)));
    return TRUE;
}
static LRESULT CALLBACK background_proc(HWND window, UINT message, WPARAM wp, LPARAM lp)
{
    if (message == WM_PAINT)
    {
        PAINTSTRUCT ps; HDC dc = BeginPaint(window,&ps);
        HBRUSH brush = CreateSolidBrush(background_color);
        FillRect(dc,&ps.rcPaint,brush); DeleteObject(brush); EndPaint(window,&ps);
        return 0;
    }
    return DefWindowProcW(window,message,wp,lp);
}
static void click(int x, int y)
{
    if (x11_input)
    {
        std::printf("XCLICK %d %d\n", x, y);
        CHECK(reply() == '\n');
        pump(500);
        GUITHREADINFO gui = {}; gui.cbSize = sizeof(gui);
        if (GetGUIThreadInfo(0,&gui))
            std::printf("after X click foreground=%p capture=%p menu_visible=%d\n",
                        gui.hwndActive,gui.hwndCapture,IsWindowVisible(menu));
        std::printf("outside buttons=%u/%u\n",outside_down,outside_up);
        return;
    }
    CHECK(SetCursorPos(x,y));
    pump(100);
    POINT actual = {}; GetCursorPos(&actual);
    HWND hit = WindowFromPoint(actual);
    wchar_t klass[128] = {}; GetClassNameW(hit,klass,128);
    std::printf("click point=%ld,%ld hit=%p class=%ls fg=%p capture=%p menu_style=%lx exstyle=%lx enabled=%d\n",
                actual.x,actual.y,hit,klass,GetForegroundWindow(),GetCapture(),
                GetWindowLongW(menu,GWL_STYLE),GetWindowLongW(menu,GWL_EXSTYLE),IsWindowEnabled(menu));
    GUITHREADINFO gui = {}; gui.cbSize = sizeof(gui);
    if (GetGUIThreadInfo(0,&gui))
        std::printf("foreground active=%p focus=%p capture=%p flags=%lx\n",
                    gui.hwndActive,gui.hwndFocus,gui.hwndCapture,gui.flags);
    INPUT inputs[2] = {};
    inputs[0].type = inputs[1].type = INPUT_MOUSE;
    inputs[0].mi.dwFlags = MOUSEEVENTF_LEFTDOWN;
    inputs[1].mi.dwFlags = MOUSEEVENTF_LEFTUP;
    CHECK(SendInput(2, inputs, sizeof(INPUT)) == 2);
    pump(500);
    std::printf("outside buttons=%u/%u\n",outside_down,outside_up);
}
static LRESULT CALLBACK outside_proc(HWND window, UINT message, WPARAM wp, LPARAM lp)
{
    if (message == WM_LBUTTONDOWN) ++outside_down;
    if (message == WM_LBUTTONUP) ++outside_up;
    return DefWindowProcW(window,message,wp,lp);
}
int main(int argc, char **argv)
{
    if (!GetEnvironmentVariableW(L"GHUB_TRAY_PRIVATE_LAB", nullptr, 0)) return 2;
    bool move_window = false, placement_only = false, surface_input = false, solid_background = false, two_monitors = false;
    for (int i = 1; i < argc; ++i)
    {
        if (!std::strcmp(argv[i], "--move-window")) move_window = true;
        else if (!std::strcmp(argv[i], "--placement-only")) placement_only = true;
        else if (!std::strcmp(argv[i], "--surface-input")) surface_input = true;
        else if (!std::strcmp(argv[i], "--solid-background")) solid_background = true;
        else if (!std::strcmp(argv[i], "--two-monitors")) two_monitors = true;
        else if (!std::strcmp(argv[i], "--x11-input")) x11_input = true;
        else if (!std::strcmp(argv[i], "--plasma-tray")) plasma_tray = true;
        else if (!std::strcmp(argv[i], "--navigation")) navigation = true;
        else return 2;
    }
    setvbuf(stdout, nullptr, _IONBF, 0);
    InitializeCriticalSection(&reply_lock);
    reply_event = CreateEventW(nullptr,TRUE,FALSE,nullptr);
    if (!reply_event) return 2;
    HANDLE reader = CreateThread(nullptr,0,read_replies,nullptr,0,nullptr);
    if (!reader) return 2;
    CloseHandle(reader);
    EnumDisplayMonitors(nullptr,nullptr,describe_monitor,0);
    const int vx = GetSystemMetrics(SM_XVIRTUALSCREEN), vy = GetSystemMetrics(SM_YVIRTUALSCREEN);
    const int vw = GetSystemMetrics(SM_CXVIRTUALSCREEN), vh = GetSystemMetrics(SM_CYVIRTUALSCREEN);
    std::printf("DESKTOP %d %d %d %d\n",vx,vy,vw,vh);
    CHECK(reply() == '\n');
    std::printf("display monitors=%d\n", GetSystemMetrics(SM_CMONITORS));
    if (two_monitors) CHECK(GetSystemMetrics(SM_CMONITORS) >= 2);
    for (int i = 0; i < 150 && !menu; ++i)
    {
        HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        PROCESSENTRY32W entry = {}; entry.dwSize = sizeof(entry);
        if (Process32FirstW(snap, &entry)) do
        {
            if (!_wcsicmp(entry.szExeFile, L"lghub_system_tray.exe"))
            { tray_pid = entry.th32ProcessID; EnumWindows(find_menu, 0); }
        } while (Process32NextW(snap, &entry));
        CloseHandle(snap);
        if (!menu) Sleep(100);
    }
    CHECK(menu);
    if (!menu) return 1;
    HWINEVENTHOOK foreground_hook = SetWinEventHook(EVENT_SYSTEM_FOREGROUND,EVENT_SYSTEM_FOREGROUND,
        nullptr,focus_event,0,0,WINEVENT_OUTOFCONTEXT);
    HWINEVENTHOOK visibility_hook = SetWinEventHook(EVENT_OBJECT_SHOW,EVENT_OBJECT_HIDE,
        nullptr,focus_event,tray_pid,0,WINEVENT_OUTOFCONTEXT);
    CHECK(foreground_hook && visibility_hook);
    HANDLE process = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, tray_pid);
    CHECK(process);
    HWND background = nullptr;
    if (surface_input)
    {
        WNDCLASSW bg = {}; bg.lpfnWndProc = background_proc; bg.lpszClassName = L"PrivateChangingBackground";
        bg.hCursor = LoadCursorW(nullptr,MAKEINTRESOURCEW(32512));
        CHECK(RegisterClassW(&bg));
        background = CreateWindowExW(WS_EX_NOACTIVATE,bg.lpszClassName,L"Private background",
            WS_POPUP|WS_VISIBLE,vx,vy,vw,vh,nullptr,nullptr,nullptr,nullptr);
        CHECK(background);
        pump(500);
    }
    WNDCLASSW wc = {}; wc.lpfnWndProc = outside_proc; wc.lpszClassName = L"PrivateOutsideTarget";
    CHECK(RegisterClassW(&wc));
    HWND outside = CreateWindowExW(0, wc.lpszClassName, L"Private outside-click target", WS_OVERLAPPEDWINDOW | WS_VISIBLE,
                                  20, 300, 250, 200, nullptr, nullptr, nullptr, nullptr);
    CHECK(outside);
    CHECK(SetWindowPos(outside, HWND_TOPMOST, 0,0,0,0,SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE));
    pump(1000);
    click(100,400);
    CHECK(outside_down == 1 && outside_up == 1);
    std::vector<POINT> anchors = {POINT{740,730}, POINT{120,40}};
    for (const RECT &monitor : monitors)
        if (monitor.left || monitor.top)
            anchors.push_back(POINT{monitor.right-100,monitor.bottom-60});
    for (POINT anchor : anchors)
    {
        if (IsWindowVisible(menu)) { PostMessageW(menu, 0x500, 0, WM_RBUTTONUP); wait_visible(false); }
        CHECK(SetCursorPos(anchor.x, anchor.y));
        pump(100);
        CHECK(open_menu());
        CHECK(wait_visible(true));
        pump(2000);
        CHECK(wait_content());
        CHECK(GetForegroundWindow() == menu);
        if (move_window)
        {
            CHECK(SetWindowPos(menu, HWND_TOPMOST, 565, 500, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE));
            pump(2000);
        }
        RECT rect = {};
        CHECK(GetWindowRect(menu, &rect));
        std::printf("native_menu pid=%lu hwnd=%p anchor=%ld,%ld rect=%ld,%ld,%ld,%ld\n", tray_pid, menu,
                    anchor.x,anchor.y,rect.left,rect.top,rect.right,rect.bottom);
        MONITORINFO monitor = {}; monitor.cbSize = sizeof(monitor);
        CHECK(GetMonitorInfoW(MonitorFromPoint(anchor, MONITOR_DEFAULTTONEAREST), &monitor));
        CHECK(rect.left >= monitor.rcWork.left && rect.top >= monitor.rcWork.top &&
              rect.right <= monitor.rcWork.right && rect.bottom <= monitor.rcWork.bottom);
        CHECK(rect.right-rect.left == 350 && rect.bottom-rect.top >= 150);
        if (surface_input)
        {
            describe(menu,0); EnumChildWindows(menu,describe,0);
            sample(rect.left+5,rect.top+20,RGB(255,255,255));
            sample(rect.right-25,rect.top+35,solid_background ? RGB(255,255,255) : background_color);
            background_color = background_color == RGB(20,80,160) ? RGB(160,60,20) : RGB(20,80,160);
            InvalidateRect(background,nullptr,FALSE); UpdateWindow(background); pump(500);
            sample(rect.right-25,rect.top+35,solid_background ? RGB(255,255,255) : background_color);
            CHECK(SetCursorPos(rect.left+90,rect.top+80)); pump(250);
            std::puts("CURSOR visible-menu"); CHECK(reply() == '\n');
        }
        checkpoint(anchor.y > 100 ? "bottom-menu" : "top-menu");
        for (int i = 0; i < 12; ++i)
        {
            CHECK(SetCursorPos(rect.left+30+i*20, rect.top+10+(i%3)*(rect.bottom-rect.top-20)/3));
            pump(100);
            CHECK(WaitForSingleObject(process,0) == WAIT_TIMEOUT);
        }
        checkpoint("hovered");
        click(100,400);
        CHECK(wait_visible(false));
        checkpoint("outside-click");
    }
    if (placement_only)
    {
        std::printf("native_menu checks=%d failures=%d scope=placement-hover-dismissal actions=not-tested\n", checks, failures);
        DestroyWindow(outside);
        UnhookWinEvent(foreground_hook); UnhookWinEvent(visibility_hook);
        CloseHandle(process);
        return failures ? 1 : 0;
    }
    if (navigation)
    {
        for (int center : {24,60,96,132})
        {
            if (HWND frontend = frontend_window()) PostMessageW(frontend,WM_CLOSE,0,0);
            CHECK(wait_frontend(false));
            SetCursorPos(740,730); pump(100);
            CHECK(open_menu()); CHECK(wait_visible(true)); CHECK(wait_content());
            RECT rect = {}; GetWindowRect(menu,&rect);
            std::printf("NAVIGATE row_center=%d\n",center);
            click(rect.left+100,rect.top+center);
            CHECK(wait_visible(false)); CHECK(wait_frontend(true));
            CHECK(WaitForSingleObject(process,0) == WAIT_TIMEOUT);
            // A newly shown Electron window is not yet finished starting.
            // Let its startup focus requests settle before testing the next close.
            pump(3000);
            checkpoint("navigation");
        }
        if (HWND frontend = frontend_window()) PostMessageW(frontend,WM_CLOSE,0,0);
        CHECK(wait_frontend(false)); pump(500);
    }
    SetCursorPos(anchors.back().x, anchors.back().y);
    pump(100);
    CHECK(open_menu());
    CHECK(wait_visible(true));
    pump(500);
    CHECK(GetForegroundWindow() == menu);
    RECT rect = {};
    GetWindowRect(menu, &rect);
    checkpoint("before-close");
    click(rect.left+100, rect.bottom-20);
    for (int i=0;i<100 && WaitForSingleObject(process,0) == WAIT_TIMEOUT;++i) pump(100);
    CHECK(WaitForSingleObject(process,0) == WAIT_OBJECT_0);
    DWORD code = STILL_ACTIVE;
    GetExitCodeProcess(process,&code);
    CHECK(code == 0);
    checkpoint("after-close");
    std::printf("native_menu checks=%d failures=%d exit=%lu\n",checks,failures,code);
    DestroyWindow(outside);
    UnhookWinEvent(foreground_hook); UnhookWinEvent(visibility_hook);
    CloseHandle(process);
    return failures ? 1 : 0;
}
