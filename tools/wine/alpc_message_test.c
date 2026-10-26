/* ALPC connection notification, acceptance, and request/reply across processes.
 * Build with MinGW: -municode -Wall -Wextra -Werror -Wno-cast-function-type.
 * Uses a unique named port; no hardware or desktop interaction.
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
typedef struct {
    USHORT data_length, total_length, type, data_offset;
    HANDLE pid, tid;
    ULONG id;
    SIZE_T view_size;
} message_header;
typedef struct { message_header header; ULONG payload[6]; } message;
typedef NTSTATUS (WINAPI *create_fn)(HANDLE *, OBJECT_ATTRIBUTES *, port_attributes *);
typedef NTSTATUS (WINAPI *connect_fn)(HANDLE *, UNICODE_STRING *, OBJECT_ATTRIBUTES *, port_attributes *,
    ULONG, PSID, message_header *, SIZE_T *, void *, void *, LARGE_INTEGER *);
typedef NTSTATUS (WINAPI *accept_fn)(HANDLE *, HANDLE, ULONG, OBJECT_ATTRIBUTES *, port_attributes *,
    void *, message_header *, void *, BOOLEAN);
typedef NTSTATUS (WINAPI *exchange_fn)(HANDLE, ULONG, message_header *, void *, message_header *, SIZE_T *, void *, LARGE_INTEGER *);
typedef NTSTATUS (WINAPI *disconnect_fn)(HANDLE, ULONG);
typedef NTSTATUS (WINAPI *packet_fn)(HANDLE *, ACCESS_MASK, OBJECT_ATTRIBUTES *);
typedef NTSTATUS (WINAPI *associate_fn)(HANDLE, HANDLE, HANDLE, void *, void *, NTSTATUS, ULONG_PTR, BOOLEAN *);
typedef NTSTATUS (WINAPI *cancel_fn)(HANDLE, BOOLEAN);

static create_fn create_port;
static connect_fn connect_port;
static accept_fn accept_port;
static exchange_fn exchange;
static disconnect_fn disconnect_port;
static packet_fn create_packet;
static associate_fn associate;
static cancel_fn cancel;
static NTSTATUS status;

#define CHECK(c) do { if (!(c)) { \
    fprintf(stderr, "FAIL %lu line %u: %s status=%08lx error=%lu\n", \
        GetCurrentProcessId(), __LINE__, #c, (ULONG)status, GetLastError()); \
    failed = 1; goto done; } } while (0)
#define CALL(c) CHECK((status = (c)) == 0)

static UNICODE_STRING make_name(WCHAR *text)
{
    UNICODE_STRING name;
    name.Buffer = text;
    name.Length = (USHORT)(wcslen(text) * sizeof(WCHAR));
    name.MaximumLength = name.Length + sizeof(WCHAR);
    return name;
}

static port_attributes make_attributes(void)
{
    port_attributes attributes = {0};
    attributes.flags = 0x10000;
    attributes.qos.Length = sizeof(attributes.qos);
    attributes.qos.ImpersonationLevel = SecurityIdentification;
    attributes.max_message = 1024;
    return attributes;
}

static int client(WCHAR *port_name, BOOL refused)
{
    int failed = 0;
    HANDLE port = NULL;
    UNICODE_STRING name = make_name(port_name);
    port_attributes attributes = make_attributes();
    LARGE_INTEGER timeout;
    message request = {0}, reply = {0};
    SIZE_T size = sizeof(request);
    timeout.QuadPart = -100000000; /* Automated test guard: 10 seconds. */
    request.header.total_length = sizeof(request);
    request.header.data_length = sizeof(request.payload);
    request.payload[0] = 0x12345678;
    status = connect_port(&port, &name, NULL, &attributes, 0x20000, NULL,
        &request.header, &size, NULL, NULL, &timeout);
    if (refused) {
        CHECK(status == (NTSTATUS)0xc0000041);
        CHECK(!port);
        goto done;
    }
    CHECK(status == 0 && port);
    CHECK(request.payload[0] == 0x87654321);
    memset(&request, 0, sizeof(request));
    request.header.total_length = sizeof(request);
    request.header.data_length = sizeof(request.payload);
    request.payload[0] = 0xfeedcafe;
    size = sizeof(reply);
    CALL(exchange(port, 0x20000, &request.header, NULL, &reply.header, &size, NULL, &timeout));
    CHECK((reply.header.type & 0xff) == 2);
    CHECK(reply.payload[0] == 0xc001cafe);
    CHECK(reply.header.data_length == sizeof(reply.payload));
    CALL(disconnect_port(port, 0));
done:
    if (port) CloseHandle(port);
    return failed;
}

static int server(BOOL refused)
{
    int failed = 0;
    WCHAR port_name[160], executable[MAX_PATH], command[1024];
    UNICODE_STRING name;
    OBJECT_ATTRIBUTES object_attributes = {0};
    port_attributes attributes = make_attributes();
    HANDLE port = NULL, communication = NULL, packet = NULL, completion = NULL;
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION child = {0};
    BOOLEAN signaled = TRUE;
    DWORD bytes, exit_code;
    ULONG_PTR key;
    OVERLAPPED *overlapped;
    message incoming = {0};
    SIZE_T size;
    LARGE_INTEGER timeout;
    timeout.QuadPart = -100000000;
    swprintf(port_name, 160, L"\\BaseNamedObjects\\PenguinAlpcMessages-%lu-%d", GetCurrentProcessId(), refused);
    name = make_name(port_name);
    object_attributes.Length = sizeof(object_attributes);
    object_attributes.ObjectName = &name;
    CALL(create_port(&port, &object_attributes, &attributes));
    completion = CreateIoCompletionPort(INVALID_HANDLE_VALUE, NULL, 0, 1);
    CHECK(completion);
    CALL(create_packet(&packet, STANDARD_RIGHTS_REQUIRED | 1, NULL));
    CALL(associate(packet, completion, port, (void *)0x1234, NULL, 0, 0, &signaled));
    CHECK(!signaled);
    CHECK(GetModuleFileNameW(NULL, executable, MAX_PATH));
    swprintf(command, 1024, L"\"%ls\" --client \"%ls\" %d", executable, port_name, refused);
    startup.cb = sizeof(startup);
    CHECK(CreateProcessW(executable, command, NULL, NULL, FALSE, 0, NULL, NULL, &startup, &child));
    CHECK(GetQueuedCompletionStatus(completion, &bytes, &key, &overlapped, 10000));
    CHECK(key == 0x1234);
    size = sizeof(incoming);
    CALL(exchange(port, 0, NULL, NULL, &incoming.header, &size, NULL, &timeout));
    CHECK((incoming.header.type & 0xff) == 10);
    CHECK(incoming.payload[0] == 0x12345678);
    CHECK((ULONG_PTR)incoming.header.pid == child.dwProcessId);
    incoming.payload[0] = 0x87654321;
    CALL(accept_port(&communication, port, 0, NULL, &attributes, (void *)0x55,
        &incoming.header, NULL, !refused));
    if (!refused) {
        CHECK(communication);
        CALL(associate(packet, completion, port, (void *)0x5678, NULL, 0, 0, &signaled));
        CHECK(GetQueuedCompletionStatus(completion, &bytes, &key, &overlapped, 10000));
        CHECK(key == 0x5678);
        /* A short receive must leave the message queued for a later retry. */
        size = sizeof(message_header);
        status = exchange(port, 0, NULL, NULL, &incoming.header, &size, NULL, &timeout);
        CHECK(status == (NTSTATUS)0xc0000023 && size == sizeof(incoming));
        CHECK(WaitForSingleObject(port, 0) == WAIT_OBJECT_0);
        size = sizeof(incoming);
        CALL(exchange(port, 0, NULL, NULL, &incoming.header, &size, NULL, &timeout));
        CHECK((incoming.header.type & 0xff) == 1 && incoming.payload[0] == 0xfeedcafe);
        incoming.payload[0] = 0xc001cafe;
        CALL(exchange(communication, 0x10000, &incoming.header, NULL, NULL, NULL, NULL, NULL));
    }
    CHECK(WaitForSingleObject(child.hProcess, 10000) == WAIT_OBJECT_0);
    CHECK(GetExitCodeProcess(child.hProcess, &exit_code) && exit_code == 0);
done:
    if (child.hProcess) {
        if (WaitForSingleObject(child.hProcess, 0) == WAIT_TIMEOUT) TerminateProcess(child.hProcess, 1);
        CloseHandle(child.hProcess);
        CloseHandle(child.hThread);
    }
    if (packet) { cancel(packet, TRUE); CloseHandle(packet); }
    if (completion) CloseHandle(completion);
    if (communication) { disconnect_port(communication, 0); CloseHandle(communication); }
    if (port) { disconnect_port(port, 0); CloseHandle(port); }
    printf("ALPC %s: %s\n", refused ? "connection rejection" : "cross-process handshake and request/reply", failed ? "FAIL" : "PASS");
    return failed;
}

int wmain(int argc, WCHAR **argv)
{
    HMODULE module = GetModuleHandleW(L"ntdll.dll");
#define LOAD(variable, type, name) variable = (type)GetProcAddress(module, name); if (!variable) return 1
    LOAD(create_port, create_fn, "NtAlpcCreatePort");
    LOAD(connect_port, connect_fn, "NtAlpcConnectPort");
    LOAD(accept_port, accept_fn, "NtAlpcAcceptConnectPort");
    LOAD(exchange, exchange_fn, "NtAlpcSendWaitReceivePort");
    LOAD(disconnect_port, disconnect_fn, "NtAlpcDisconnectPort");
    LOAD(create_packet, packet_fn, "NtCreateWaitCompletionPacket");
    LOAD(associate, associate_fn, "NtAssociateWaitCompletionPacket");
    LOAD(cancel, cancel_fn, "NtCancelWaitCompletionPacket");
#undef LOAD
    if (sizeof(message_header) != 40 || sizeof(message) != 64) return 1;
    if (argc == 4 && !wcscmp(argv[1], L"--client")) return client(argv[2], _wtoi(argv[3]));
    return server(FALSE) | server(TRUE);
}
