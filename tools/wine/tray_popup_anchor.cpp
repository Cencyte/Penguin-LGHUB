/* Opt-in host placement for the original G HUB tray executable. Preserve its
 * XAML layout and move only its Win32 presentation window near the tray click.
 * Hooks and subclasses are limited to this process and the owning UI thread. */
#include <windows.h>
#include <commctrl.h>
#include <new>
#include <cstdio>
#include <algorithm>
#include <cstdarg>
#include <cwchar>

struct Anchor { POINT point; };
static constexpr UINT_PTR subclass_id = 0x4748414e;
static constexpr wchar_t anchor_property[] = L"Ghub.Private.TrayAnchor.1";

static void trace(const char *format, ...)
{
    if (!GetEnvironmentVariableW(L"GHUB_TRAY_ANCHOR_TRACE", nullptr, 0)) return;
    wchar_t path[1024];
    DWORD length = GetEnvironmentVariableW(L"GHUB_TRAY_ANCHOR_LOG", path, 1024);
    FILE *file = length && length < 1024 ? _wfopen(path, L"a") : nullptr;
    va_list args;
    va_start(args, format);
    std::vfprintf(file ? file : stderr, format, args);
    va_end(args);
    if (file) std::fclose(file);
}

static LONG clamp(LONG value, LONG low, LONG high)
{
    return value < low ? low : value > high ? high : value;
}

/* A RECT-shaped WinRT RectInt32: right/bottom contain WIDTH/HEIGHT here. */
static BOOL place(POINT point, const RECT &work, RECT *rect)
{
    if (!rect || rect->right <= 0 || rect->bottom <= 0 ||
        work.right <= work.left || work.bottom <= work.top) return FALSE;
    const LONG width = std::min(rect->right, work.right - work.left);
    const LONG height = std::min(rect->bottom, work.bottom - work.top);
    LONG x = point.x - width / 2, y;
    if (point.y >= work.bottom) y = work.bottom - height;
    else if (point.y <= work.top) y = work.top;
    else if (point.x <= work.left) { x = work.left; y = point.y - height / 2; }
    else if (point.x >= work.right) { x = work.right - width; y = point.y - height / 2; }
    else y = point.y + 4 + height <= work.bottom ? point.y + 4 : point.y - height - 4;
    *rect = {clamp(x, work.left, work.right - width), clamp(y, work.top, work.bottom - height), width, height};
    return TRUE;
}

static LRESULT CALLBACK anchor_proc(HWND window, UINT message, WPARAM wp, LPARAM lp,
                                    UINT_PTR id, DWORD_PTR data)
{
    auto *anchor = reinterpret_cast<Anchor *>(data);
    if (message == WM_SETCURSOR && LOWORD(lp) == HTCLIENT)
    {
        trace("tray cursor hwnd=%p\n",window);
        SetCursor(LoadCursorW(nullptr, MAKEINTRESOURCEW(32512)));
        return TRUE;
    }
    auto *pos = message == WM_WINDOWPOSCHANGING ? reinterpret_cast<WINDOWPOS *>(lp) : nullptr;
    if ((message == WM_SHOWWINDOW && wp) ||
        (pos && (pos->flags & SWP_SHOWWINDOW) && !IsWindowVisible(window)))
        GetCursorPos(&anchor->point);
    if (pos && !(pos->flags & SWP_HIDEWINDOW))
    {
        RECT current = {};
        MONITORINFO monitor = {sizeof(monitor), {}, {}, 0};
        if (GetWindowRect(window, &current) &&
            GetMonitorInfoW(MonitorFromPoint(anchor->point, MONITOR_DEFAULTTONEAREST), &monitor))
        {
            RECT desired = {0, 0, (pos->flags & SWP_NOSIZE) ? current.right-current.left : pos->cx,
                                 (pos->flags & SWP_NOSIZE) ? current.bottom-current.top : pos->cy};
            if (place(anchor->point, monitor.rcWork, &desired))
            {
                pos->x = desired.left; pos->y = desired.top;
                pos->cx = desired.right; pos->cy = desired.bottom;
                pos->flags &= ~(SWP_NOMOVE | SWP_NOSIZE);
                trace("tray_anchor hwnd=%p point=%ld,%ld presentation=%ld,%ld,%ld,%ld\n", window,
                      anchor->point.x, anchor->point.y, pos->x,pos->y,pos->cx,pos->cy);
            }
        }
    }
    if (message == WM_NCDESTROY)
    {
        RemoveWindowSubclass(window, anchor_proc, id);
        RemovePropW(window, anchor_property);
        delete anchor;
    }
    return DefSubclassProc(window, message, wp, lp);
}

static thread_local HHOOK create_hook;
static LRESULT CALLBACK cursor_proc(HWND window, UINT message, WPARAM wp, LPARAM lp,
                                    UINT_PTR id, DWORD_PTR)
{
    if (message == WM_SETCURSOR && LOWORD(lp) == HTCLIENT)
    {
        SetCursor(LoadCursorW(nullptr, MAKEINTRESOURCEW(32512)));
        return TRUE;
    }
    if (message == WM_NCDESTROY) RemoveWindowSubclass(window,cursor_proc,id);
    return DefSubclassProc(window,message,wp,lp);
}
static void attach_window(HWND window)
{
    wchar_t name[128] = {};
    GetClassNameW(window, name, 128);
    if (!wcscmp(name,L"Microsoft.UI.Content.DesktopChildSiteBridge"))
        SetWindowSubclass(window,cursor_proc,subclass_id,0);
    if (!wcscmp(name, L"WinUIDesktopWin32WindowClass") && !GetPropW(window, anchor_property))
    {
        auto *anchor = new (std::nothrow) Anchor{};
        if (anchor)
        {
            GetCursorPos(&anchor->point);
            if (!SetPropW(window, anchor_property, anchor) ||
                !SetWindowSubclass(window, anchor_proc, subclass_id, reinterpret_cast<DWORD_PTR>(anchor)))
            { RemovePropW(window, anchor_property); delete anchor; }
            else
            {
                // The XAML tree supplies the entire popup image, including its
                // transparent margin. There must be no stale GDI backing image.
                SetWindowLongPtrW(window, GWL_EXSTYLE, GetWindowLongPtrW(window, GWL_EXSTYLE) |
                                  WS_EX_NOREDIRECTIONBITMAP);
                trace("tray_anchor attached hwnd=%p\n", window);
            }
        }
    }
}

static LRESULT CALLBACK creation_hook(int code, WPARAM wp, LPARAM lp)
{
    if (code == HCBT_CREATEWND || code == HCBT_ACTIVATE) attach_window(reinterpret_cast<HWND>(wp));
    return CallNextHookEx(create_hook, code, wp, lp);
}

static BOOL CALLBACK attach_child(HWND window, LPARAM)
{
    attach_window(window);
    return TRUE;
}

static BOOL CALLBACK attach_existing(HWND window, LPARAM)
{
    attach_window(window);
    EnumChildWindows(window,attach_child,0);
    return TRUE;
}

void ghub_install_tray_anchor()
{
    wchar_t enabled[2], path[1024];
    if (create_hook || GetEnvironmentVariableW(L"GHUB_TRAY_ANCHOR", enabled, 2) != 1 || enabled[0] != L'1') return;
    DWORD length = GetModuleFileNameW(nullptr, path, 1024);
    if (!length || length >= 1024) return;
    const wchar_t *name = wcsrchr(path, L'\\');
    if (!name || _wcsicmp(name+1, L"lghub_system_tray.exe")) return;
    create_hook = SetWindowsHookExW(WH_CBT, creation_hook, nullptr, GetCurrentThreadId());
    trace("tray_anchor hook=%p error=%lu\n", create_hook, GetLastError());
    EnumThreadWindows(GetCurrentThreadId(), attach_existing, 0);
}
