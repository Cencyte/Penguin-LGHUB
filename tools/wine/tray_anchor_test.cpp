// Pure geometry plus actual subclass/reopen/resize checks on private Xvfb.
#include "tray_popup_anchor.cpp"
static unsigned checks, failures;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; std::printf("FAIL line %d: %s\n", __LINE__, #x); } } while (0)
static HWND window;
static RECT desired;
static void resize(int width, int height)
{
    CHECK(SetWindowPos(window, nullptr, 0, 52, width, height,
                       SWP_NOACTIVATE | SWP_NOZORDER));
    CHECK(GetWindowRect(window, &desired));
    desired.right -= desired.left;
    desired.bottom -= desired.top;
}
int main()
{
    const RECT works[] = {{0, 0, 1920, 1040}, {0, 1080, 1920, 2120}, {-1920, -1080, 0, -40},
                          {40, 0, 1920, 1080}, {0, 40, 1920, 1080}};
    for (const auto &work : works)
        for (POINT point : {POINT{work.left-10,work.top+200}, POINT{work.right+10,work.top+200},
                            POINT{work.left+200,work.top-10}, POINT{work.left+200,work.bottom+10},
                            POINT{work.left+200,work.top+200}})
        {
            RECT r = {0, 0, 350, 314};
            CHECK(place(point, work, &r));
            CHECK(r.left >= work.left && r.top >= work.top);
            CHECK(r.left+r.right <= work.right && r.top+r.bottom <= work.bottom);
            CHECK(r.right == 350 && r.bottom == 314);
            LONG dx = std::max({r.left-point.x, point.x-(r.left+r.right), 0L});
            LONG dy = std::max({r.top-point.y, point.y-(r.top+r.bottom), 0L});
            CHECK(dx <= 10 && dy <= 10);
        }
    RECT r = {99, 99, 5000, 4000};
    CHECK(place({0, 0}, {0, 0, 1024, 768}, &r));
    CHECK(r.left == 0 && r.top == 0 && r.right == 1024 && r.bottom == 768);
    CHECK(!place({0,0}, {0,0,0,0}, &r));
    CHECK(!place({0,0}, {0,0,100,100}, nullptr));
    WNDCLASSW klass = {};
    klass.lpfnWndProc = DefWindowProcW;
    klass.lpszClassName = L"WinUIDesktopWin32WindowClass";
    CHECK(RegisterClassW(&klass));
    window = CreateWindowExW(0, klass.lpszClassName, L"Private anchor fixture", WS_POPUP, 0,0,350,314,
                             nullptr,nullptr,nullptr,nullptr);
    CHECK(window != nullptr);
    CHECK(SetCursorPos(740, 730));
    attach_window(window);
    CHECK(GetPropW(window, anchor_property));
    CHECK(GetWindowLongW(window,GWL_EXSTYLE)&WS_EX_NOREDIRECTIONBITMAP);
    SetCursor(nullptr);
    CHECK(SendMessageW(window,WM_SETCURSOR,reinterpret_cast<WPARAM>(window),MAKELPARAM(HTCLIENT,WM_MOUSEMOVE)));
    CHECK(GetCursor()==LoadCursorW(nullptr,MAKEINTRESOURCEW(32512)));
    resize(350, 314);
    RECT initial = desired;
    ShowWindow(window, SW_SHOWNOACTIVATE);
    CHECK(SetCursorPos(400, 300));
    resize(350, 400);
    CHECK(desired.left == initial.left); // pointer motion must not move the anchor
    CHECK(desired.top+desired.bottom == initial.top+initial.bottom);
    ShowWindow(window, SW_HIDE);
    CHECK(SetCursorPos(100, 40));
    ShowWindow(window, SW_SHOWNOACTIVATE);
    resize(350, 314);
    CHECK(desired.left == 0 && desired.top == 44);
    CHECK(DestroyWindow(window));
    CHECK(!GetPropW(window, anchor_property));
    window = CreateWindowExW(0, L"Static", L"Unrelated fixture", WS_POPUP, 10,20,30,40,
                            nullptr,nullptr,nullptr,nullptr);
    attach_window(window);
    CHECK(!GetPropW(window, anchor_property));
    CHECK(DestroyWindow(window));
    std::printf("tray_anchor checks=%u failures=%u\n", checks, failures);
    return !!failures;
}
