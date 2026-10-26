/* The diagnostic WNF export must fail without a token or callback delivery.
 * ABI reference: https://github.com/winsiderss/phnt/blob/master/ntrtl.h
 * This is not a WNF implementation/conformance test.
 */
#include <windows.h>
#include <stdio.h>

typedef LONG (WINAPI *wnf_callback)(ULONGLONG, ULONG, const GUID *, void *, const void *, ULONG);
typedef LONG (WINAPI *subscribe_fn)(void **, ULONGLONG, ULONG, wnf_callback,
        void *, const GUID *, ULONG, ULONG);
static LONG callbacks;

static LONG WINAPI callback(ULONGLONG state, ULONG stamp, const GUID *type,
        void *context, const void *buffer, ULONG length)
{
    (void)state; (void)stamp; (void)type; (void)context; (void)buffer; (void)length;
    InterlockedIncrement(&callbacks);
    return 0;
}

int main(void)
{
    subscribe_fn subscribe = (subscribe_fn)(void *)GetProcAddress(
            GetModuleHandleW(L"ntdll.dll"), "RtlSubscribeWnfStateChangeNotification");
    void *token = (void *)(UINT_PTR)0xdeadbeef;
    LONG status;
    if (!subscribe) { puts("FAIL: missing WNF subscription export"); return 1; }
    status = subscribe(&token, 0x41c6013da3bcb875ULL, 0, callback, &callbacks, NULL, 0, 0);
    printf("WNF status=%08lx token=%p callbacks=%ld\n", status, token, callbacks);
    if (status != (LONG)0xc0000002 || token || callbacks) return 1;
    Sleep(50);
    if (callbacks) return 1;
    puts("PASS: unsupported WNF reports failure without fabricating a subscription");
    return 0;
}
