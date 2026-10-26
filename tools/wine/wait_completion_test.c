/* Focused probe for the optional, experimental Wine wait-completion backport.
 * Build with -Wall -Wextra -Werror -Wno-cast-function-type.
 * Tests kernel event/IOCP semantics; does not interact with input devices.
 */
#include <windows.h>
#include <winternl.h>
#include <stdio.h>

typedef NTSTATUS (WINAPI *create_fn)(HANDLE *, ACCESS_MASK, OBJECT_ATTRIBUTES *);
typedef NTSTATUS (WINAPI *associate_fn)(HANDLE, HANDLE, HANDLE, void *, void *,
                                       NTSTATUS, ULONG_PTR, BOOLEAN *);
typedef NTSTATUS (WINAPI *cancel_fn)(HANDLE, BOOLEAN);

#define CHECK(condition) do { if (!(condition)) { \
    fprintf(stderr, "FAIL line %u: %s (error %lu)\n", __LINE__, #condition, GetLastError()); \
    failed = 1; goto done; } } while (0)

int main(void)
{
    int failed = 0;
    HANDLE packet = NULL, event = NULL, port = NULL;
    DWORD bytes;
    ULONG_PTR key;
    OVERLAPPED *overlapped;
    BOOLEAN signaled = TRUE;
    HMODULE module = GetModuleHandleW(L"ntdll.dll");
    create_fn create = (create_fn)GetProcAddress(module, "NtCreateWaitCompletionPacket");
    associate_fn associate = (associate_fn)GetProcAddress(module, "NtAssociateWaitCompletionPacket");
    cancel_fn cancel = (cancel_fn)GetProcAddress(module, "NtCancelWaitCompletionPacket");

    CHECK(create && associate && cancel);
    event = CreateEventW(NULL, FALSE, FALSE, NULL);
    CHECK(event);
    port = CreateIoCompletionPort(INVALID_HANDLE_VALUE, NULL, 0, 1);
    CHECK(port);
    CHECK(create(&packet, STANDARD_RIGHTS_REQUIRED | 1, NULL) == 0);

    /* The two context pointers are opaque cookies, never dereferenced. */
    CHECK(associate(packet, port, event, (void *)0x1234, (void *)0x5678, 0, 42, &signaled) == 0);
    CHECK(!signaled);
    CHECK(SetEvent(event));
    CHECK(GetQueuedCompletionStatus(port, &bytes, &key, &overlapped, 1000));
    CHECK(bytes == 42 && key == 0x1234 && overlapped == (OVERLAPPED *)0x5678);

    /* Cancellation must stop delivery when the target is signaled later. */
    CHECK(associate(packet, port, event, (void *)0x1234, (void *)0x5678, 0, 43, &signaled) == 0);
    CHECK(cancel(packet, FALSE) == 0);
    CHECK(SetEvent(event));
    CHECK(!GetQueuedCompletionStatus(port, &bytes, &key, &overlapped, 30));
    CHECK(GetLastError() == WAIT_TIMEOUT);

    /* An already-signaled target completes immediately on association. */
    CHECK(associate(packet, port, event, (void *)0x1234, (void *)0x5678, 0, 44, &signaled) == 0);
    CHECK(signaled);
    CHECK(GetQueuedCompletionStatus(port, &bytes, &key, &overlapped, 1000));
    CHECK(bytes == 44 && key == 0x1234 && overlapped == (OVERLAPPED *)0x5678);

done:
    if (packet) CloseHandle(packet);
    if (port) CloseHandle(port);
    if (event) CloseHandle(event);
    printf("wait completion packet: %s\n", failed ? "FAIL" : "PASS");
    return failed;
}
