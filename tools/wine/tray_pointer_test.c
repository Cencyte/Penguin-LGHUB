/* Run only on the test runner's private display/prefix. */
#include <windows.h>
#include <stdio.h>
#include <string.h>

typedef BOOL (WINAPI *transform_fn)(UINT32, UINT32, void *);
static int checks, failures;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; printf("FAIL line %d: %s\n", __LINE__, #x); } } while (0)

int main(void)
{
    HMODULE user = GetModuleHandleW(L"user32.dll");
    HMODULE api = LoadLibraryW(L"api-ms-win-rtcore-ntuser-wmpointer-l1-1-0.dll");
    transform_fn call = (transform_fn)GetProcAddress(user, "GetPointerInputTransform");
    unsigned char output[2 * 64 + 32], before[sizeof(output)];
    HWND window;
    POINT point = {335, 248};
    RECT rect;
    CHECK(api != NULL);
    CHECK(call != NULL);
    if (!call) return 1;
    CHECK(GetProcAddress(api, "GetPointerInputTransform") == (FARPROC)call);
    memset(output, 0xa7, sizeof(output));
    memcpy(before, output, sizeof(output));
    SetLastError(0xdeadbeef);
    CHECK(!call(1, 1, output+16));
    CHECK(GetLastError() == ERROR_NO_DATA);
    CHECK(!memcmp(before, output, sizeof(output)));
    CHECK(!call(0, 1, output+16)); CHECK(GetLastError() == ERROR_INVALID_PARAMETER);
    CHECK(!call(1, 0, output+16)); CHECK(GetLastError() == ERROR_INVALID_PARAMETER);
    CHECK(!call(1, 1, NULL)); CHECK(GetLastError() == ERROR_INVALID_PARAMETER);
    CHECK(!memcmp(before, output, sizeof(output)));
    window = CreateWindowExW(0, L"Static", L"Private coordinate fallback", WS_POPUP, 300, 200, 100, 100,
                             NULL, NULL, NULL, NULL);
    CHECK(window != NULL);
    CHECK(GetWindowRect(window, &rect));
    CHECK(rect.left == 300 && rect.top == 200);
    if (!call(1, 1, output+16)) CHECK(ScreenToClient(window, &point));
    CHECK(point.x == 35 && point.y == 48); // identity success would have broken this
    CHECK(DestroyWindow(window));
    FreeLibrary(api);
    printf("pointer_transform checks=%d failures=%d\n", checks, failures);
    return !!failures;
}
