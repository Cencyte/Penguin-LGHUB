/* Exercise real ALPC object lifetimes and message-attribute layout.
 * Reference: Wine's dlls/ntdll/tests/alpc.c, wine-11.17.
 * Build: x86_64-w64-mingw32-gcc -Wall -Wextra -Werror
 *        -Wno-cast-function-type alpc_port_test.c -o alpc_port_test.exe
 * No messages, hardware writes, or desktop input are generated.
 */
#include <windows.h>
#include <winternl.h>
#include <stdio.h>
#include <wchar.h>

typedef struct {
    ULONG flags;
    SECURITY_QUALITY_OF_SERVICE qos;
    SIZE_T max_message, bandwidth, pool, section, view, total_section;
    ULONG dup_types, reserved;
} port_attributes;
typedef struct { ULONG allocated, valid; } message_attributes;
typedef NTSTATUS (WINAPI *create_fn)(HANDLE *, OBJECT_ATTRIBUTES *, port_attributes *);
typedef SIZE_T (WINAPI *size_fn)(ULONG);
typedef NTSTATUS (WINAPI *init_fn)(ULONG, message_attributes *, SIZE_T, SIZE_T *);
typedef void *(WINAPI *get_fn)(message_attributes *, ULONG);
typedef NTSTATUS (WINAPI *query_fn)(HANDLE, OBJECT_INFORMATION_CLASS, void *, ULONG, ULONG *);
typedef NTSTATUS (WINAPI *packet_fn)(HANDLE *, ACCESS_MASK, OBJECT_ATTRIBUTES *);
typedef NTSTATUS (WINAPI *associate_fn)(HANDLE, HANDLE, HANDLE, void *, void *, NTSTATUS, ULONG_PTR, BOOLEAN *);
typedef NTSTATUS (WINAPI *cancel_fn)(HANDLE, BOOLEAN);

#define CHECK(c) do { if (!(c)) { \
    fprintf(stderr, "FAIL line %u: %s (error %lu)\n", __LINE__, #c, GetLastError()); \
    failed = 1; goto done; } } while (0)

int main(void)
{
    int failed = 0;
    HANDLE port = NULL, duplicate = NULL, other = NULL;
    HANDLE packet = NULL, completion = NULL;
    HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
    create_fn create = (create_fn)GetProcAddress(ntdll, "NtAlpcCreatePort");
    size_fn size = (size_fn)GetProcAddress(ntdll, "AlpcGetHeaderSize");
    init_fn init = (init_fn)GetProcAddress(ntdll, "AlpcInitializeMessageAttribute");
    get_fn get = (get_fn)GetProcAddress(ntdll, "AlpcGetMessageAttribute");
    query_fn query = (query_fn)GetProcAddress(ntdll, "NtQueryObject");
    packet_fn create_packet = (packet_fn)GetProcAddress(ntdll, "NtCreateWaitCompletionPacket");
    associate_fn associate = (associate_fn)GetProcAddress(ntdll, "NtAssociateWaitCompletionPacket");
    cancel_fn cancel = (cancel_fn)GetProcAddress(ntdll, "NtCancelWaitCompletionPacket");
    WCHAR name_buffer[128];
    UNICODE_STRING name;
    OBJECT_ATTRIBUTES attributes = {0};
    port_attributes port_attr = {0};
    union { ULONGLONG align; unsigned char bytes[1024]; } storage;
    message_attributes *message = (message_attributes *)storage.bytes;
    SIZE_T required;
    ULONG returned;
    UNICODE_STRING *type;
    BOOLEAN signaled = TRUE;
    DWORD bytes;
    ULONG_PTR key;
    OVERLAPPED *overlapped;

    CHECK(sizeof(void *) == 8 && sizeof(port_attr) == 72);
    CHECK(create && size && init && get && query);
    CHECK(create_packet && associate && cancel);
    CHECK(size(0) == 8 && size(0x80000000) == 32 && size(0xfe000000) == 160);
    required = 0;
    CHECK(init(0xfe000000, NULL, 0, &required) == (NTSTATUS)0xc0000023);
    CHECK(required == 160);
    memset(storage.bytes, 0xcc, sizeof(storage.bytes));
    CHECK(init(0xfe000000, message, sizeof(storage.bytes), &required) == 0);
    CHECK(message->allocated == 0xfe000000 && message->valid == 0);
    CHECK(get(message, 0x80000000) == storage.bytes + 8);
    CHECK(get(message, 0x20000000) == storage.bytes + 64);
    CHECK(get(message, 0x02000000) == storage.bytes + 152);
    CHECK(!get(message, 0) && !get(message, 0xc0000000) && !get(message, 1));

    swprintf(name_buffer, 128, L"\\BaseNamedObjects\\PenguinAlpcProbe-%lu", GetCurrentProcessId());
    name.Buffer = name_buffer;
    name.Length = (USHORT)(wcslen(name_buffer) * sizeof(WCHAR));
    name.MaximumLength = name.Length + sizeof(WCHAR);
    attributes.Length = sizeof(attributes);
    attributes.ObjectName = &name;
    port_attr.flags = 0x70000; /* CoreMessagingXP's actual port flags. */
    port_attr.qos.Length = sizeof(port_attr.qos);
    port_attr.qos.ImpersonationLevel = SecurityIdentification;
    port_attr.max_message = 0x200;
    CHECK(create(NULL, &attributes, &port_attr) == (NTSTATUS)0xc0000005);
    CHECK(create(&port, NULL, NULL) == 0 && port);
    CHECK(CloseHandle(port)); port = NULL;
    CHECK(create(&port, &attributes, &port_attr) == 0 && port);
    /* CoreMessagingXP immediately attaches a wait packet to its empty port.
     * This previously crashed wineserver through a NULL signaled callback. */
    CHECK(WaitForSingleObject(port, 0) == WAIT_TIMEOUT);
    completion = CreateIoCompletionPort(INVALID_HANDLE_VALUE, NULL, 0, 1);
    CHECK(completion);
    CHECK(create_packet(&packet, STANDARD_RIGHTS_REQUIRED | 1, NULL) == 0);
    CHECK(associate(packet, completion, port, (void *)0x1234, NULL, 0, 0, &signaled) == 0);
    CHECK(!signaled);
    CHECK(!GetQueuedCompletionStatus(completion, &bytes, &key, &overlapped, 30));
    CHECK(GetLastError() == WAIT_TIMEOUT);
    CHECK(cancel(packet, FALSE) == 0);
    CHECK(CloseHandle(packet)); packet = NULL;
    CHECK(CloseHandle(completion)); completion = NULL;
    CHECK(query(port, ObjectTypeInformation, storage.bytes, sizeof(storage.bytes), &returned) == 0);
    type = (UNICODE_STRING *)storage.bytes;
    CHECK(type->Length == 18 && !wmemcmp(type->Buffer, L"ALPC Port", 9));
    CHECK(create(&other, &attributes, &port_attr) == (NTSTATUS)0xc0000035);
    CHECK(!other);
    CHECK(DuplicateHandle(GetCurrentProcess(), port, GetCurrentProcess(), &duplicate, 0, FALSE, DUPLICATE_SAME_ACCESS));
    CHECK(CloseHandle(port)); port = NULL;
    CHECK(create(&other, &attributes, &port_attr) == (NTSTATUS)0xc0000035);
    CHECK(CloseHandle(duplicate)); duplicate = NULL;
    CHECK(create(&port, &attributes, &port_attr) == 0 && port);
    CHECK(CloseHandle(port)); port = NULL;
    port_attr.flags = 0x100000;
    CHECK(create(&port, &attributes, &port_attr) == (NTSTATUS)0xc000000d);
    CHECK(!port);
    attributes.Length = 1;
    port_attr.flags = 0;
    CHECK(create(&port, &attributes, &port_attr) == (NTSTATUS)0xc000000d);

done:
    if (packet) CloseHandle(packet);
    if (completion) CloseHandle(completion);
    if (port) CloseHandle(port);
    if (duplicate) CloseHandle(duplicate);
    if (other) CloseHandle(other);
    printf("ALPC object and attribute tests: %s\n", failed ? "FAIL" : "PASS");
    return failed;
}
