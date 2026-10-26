/* Catch mismatched Wine client/server request numbers in a partial build.
 * Run on Xvfb: creates menus and an unshown window, never sends input.
 * Build with -Wall -Wextra -Werror -luser32.
 */
#include <windows.h>
#include <stdio.h>

int main(void)
{
    int failed = 0;
    HMENU menu = CreateMenu(), popup = CreatePopupMenu();
    HWND window = CreateWindowExW(0, L"STATIC", L"Wine protocol regression",
        WS_OVERLAPPEDWINDOW, 0, 0, 160, 80, NULL, NULL, GetModuleHandleW(NULL), NULL);
    if (!menu || !popup || !window) {
        fprintf(stderr, "FAIL: menu=%p popup=%p window=%p error=%lu\n",
                (void *)menu, (void *)popup, (void *)window, GetLastError());
        failed = 1;
    }
    if (popup && (!AppendMenuW(popup, MF_STRING, 42, L"Item")
                  || GetMenuItemCount(popup) != 1 || GetMenuItemID(popup, 0) != 42)) {
        fprintf(stderr, "FAIL: menu item round trip\n");
        failed = 1;
    }
    if (window && !GetSystemMenu(window, FALSE)) {
        fprintf(stderr, "FAIL: system menu\n");
        failed = 1;
    }
    if (window) DestroyWindow(window);
    if (popup) DestroyMenu(popup);
    if (menu) DestroyMenu(menu);
    printf("window/server protocol: %s\n", failed ? "FAIL" : "PASS");
    return failed;
}
