/* Private-display test only. Match Electron's ShowWindow(SW_MINIMIZE) path. */
#include <windows.h>
#include <stdio.h>
#include <string.h>
static HWND target;
static BOOL CALLBACK find_window(HWND hwnd, LPARAM ignored)
{
    WCHAR title[128], cls[128];
    (void)ignored;
    GetWindowTextW(hwnd, title, 128);
    GetClassNameW(hwnd, cls, 128);
    if (!wcscmp(cls, L"Chrome_WidgetWin_1") &&
        !wcscmp(title, L"Logitech G HUB")) target = hwnd;
    return TRUE;
}
int main(int argc, char **argv)
{
    char marker[4]; RECT r;
    if (GetEnvironmentVariableA("PENGUIN_ISOLATED_UI_PROBE", marker, 4) != 1 ||
        marker[0] != '1' || argc != 2) return 2;
    EnumWindows(find_window, 0);
    if (!target) return 3;
    if (!strcmp(argv[1], "minimize")) ShowWindow(target, SW_MINIMIZE);
    else if (!strcmp(argv[1], "close")) PostMessageW(target, WM_CLOSE, 0, 0);
    else if (strcmp(argv[1], "query")) return 2;
    GetWindowRect(target, &r);
    printf("STATE iconic=%d visible=%d size=%ldx%ld\n", IsIconic(target),
           IsWindowVisible(target), r.right-r.left, r.bottom-r.top);
    return 0;
}
