/* Synthetic assignment packets, only on the explicitly owned test display. */
#include <windows.h>
#include <stdio.h>
#include <string.h>

static unsigned failures;
static unsigned received;
static LRESULT CALLBACK window_proc(HWND window, UINT msg, WPARAM wp, LPARAM lp)
{
    if (msg==WM_KEYDOWN || msg==WM_KEYUP || msg==WM_SYSKEYDOWN || msg==WM_SYSKEYUP)
    {
        ++received;
        printf("WINE_KEY %x %llx %llx\n",msg,(unsigned long long)wp,(unsigned long long)lp);
    }
    return DefWindowProcW(window,msg,wp,lp);
}
static void pump(DWORD ms)
{
    ULONGLONG end=GetTickCount64()+ms;
    do {
        MSG msg;
        while (PeekMessageW(&msg,NULL,0,0,PM_REMOVE)) DispatchMessageW(&msg);
        Sleep(1);
    } while(GetTickCount64()<end);
}
static void key(WORD vk, WORD scan, DWORD flags)
{
    INPUT input = {0};
    input.type = INPUT_KEYBOARD;
    input.ki.wVk = vk; input.ki.wScan = scan; input.ki.dwFlags = flags;
    UINT sent = SendInput(1, &input, sizeof(input));
    printf("INPUT vk=%x scan=%x flags=%lx sent=%u error=%lu\n",vk,scan,flags,sent,GetLastError());
    failures += sent != 1;
    pump(20);
}
static void pair(WORD vk, WORD scan, DWORD flags)
{
    key(vk,scan,flags); key(vk,scan,flags|KEYEVENTF_KEYUP);
}
int main(int argc, char **argv)
{
    if (!GetEnvironmentVariableA("GHUB_HOST_INPUT_PRIVATE_TEST",NULL,0)) return 2;
    BOOL wine_target=argc==2 && !strcmp(argv[1],"--wine-target");
    HWND window=NULL;
    if (wine_target)
    {
        WNDCLASSW klass={0}; klass.lpfnWndProc=window_proc; klass.lpszClassName=L"PrivateKeyReceiver";
        if (!RegisterClassW(&klass)) return 2;
        window=CreateWindowW(klass.lpszClassName,L"Private Wine keyboard receiver",WS_OVERLAPPEDWINDOW,
                             20,20,400,200,NULL,NULL,NULL,NULL);
        if (!window) return 2;
        ShowWindow(window,SW_SHOW); SetForegroundWindow(window); SetFocus(window); pump(500);
        puts("WINE_READY"); fflush(stdout);
        if (getchar()!='\n') return 2;
        pump(200);
    }
    /* G HUB supplies both wVk and wScan for ordinary HID assignments. */
    pair(VK_OEM_4,0x1a,0);
    pair(VK_OEM_6,0x1b,0);
    pair('0',0x0b,0);
    pair(VK_DELETE,0x153,KEYEVENTF_EXTENDEDKEY);
    key(VK_RCONTROL,0x11d,KEYEVENTF_EXTENDEDKEY);
    pair('A',0x1e,0);
    key(VK_RCONTROL,0x11d,KEYEVENTF_EXTENDEDKEY|KEYEVENTF_KEYUP);
    pair(VK_LEFT,0x14b,KEYEVENTF_EXTENDEDKEY);
    pair(0,0x1e,KEYEVENTF_SCANCODE);
    pair(0,0x1c,KEYEVENTF_SCANCODE);
    pair(0,0x11c,KEYEVENTF_SCANCODE|KEYEVENTF_EXTENDEDKEY);
    pair(VK_F12,0,0);
    if (wine_target)
    {
        pump(500);
        printf("WINE_RECEIVED %u expected=22\n",received);
        failures += received!=22;
        DestroyWindow(window);
    }
    return failures ? 1 : 0;
}
