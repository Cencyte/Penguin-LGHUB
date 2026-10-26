/* Used only with the owned Electron fixture on a private Xvfb display. */
#include <windows.h>
#include <wchar.h>

int wmain(int argc, wchar_t **argv)
{
    HWND window;
    WPARAM command;
    if (argc != 2) return 2;
    window = FindWindowW(NULL, L"GHUB Window Regression");
    if (!window) return 3;
    if (!wcscmp(argv[1], L"stale-mouse")) command = SC_MOVE | HTCAPTION;
    else if (!wcscmp(argv[1], L"keyboard-move")) command = SC_MOVE;
    else if (!wcscmp(argv[1], L"keyboard-size")) command = SC_SIZE;
    else return 2;
    return PostMessageW(window, WM_SYSCOMMAND, command, 0) ? 0 : 4;
}
