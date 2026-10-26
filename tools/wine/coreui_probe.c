/* Headless diagnostic for a user-supplied native CoreMessaging.dll. Never
 * deploys DLLs. --trace-connect replaces just this module's in-memory IAT entry
 * to record the unsupported NtAlpcConnectPortEx request and return NOT_IMPLEMENTED.
 * It does not pretend that the connection succeeded. */
#define COBJMACROS
#include <windows.h>
#include <winternl.h>
#include <objbase.h>
#include <sddl.h>
#include <stdio.h>
#include <wchar.h>

static BOOL forward_simple;

static void attributes(const char *label, OBJECT_ATTRIBUTES *value)
{
    fprintf(stderr, "%s: %p", label, (void *)value);
    if (value) {
        fprintf(stderr, " length=%lu root=%p flags=%08lx name=", value->Length, value->RootDirectory, value->Attributes);
        if (value->ObjectName && value->ObjectName->Buffer)
            fprintf(stderr, "%.*ls", value->ObjectName->Length / 2, value->ObjectName->Buffer);
        fprintf(stderr, " sd=%p qos=%p", value->SecurityDescriptor, value->SecurityQualityOfService);
    }
    fputc('\n', stderr);
}

static NTSTATUS WINAPI trace_connect(HANDLE *port, OBJECT_ATTRIBUTES *connection, OBJECT_ATTRIBUTES *client,
    void *port_attributes, ULONG flags, PSECURITY_DESCRIPTOR security, void *message, SIZE_T *length,
    void *send_attributes, void *receive_attributes, LARGE_INTEGER *timeout)
{
    WCHAR *sddl = NULL;
    fprintf(stderr, "NtAlpcConnectPortEx: output=%p port_attrs=%p flags=%08lx security=%p message=%p length=%llu send=%p receive=%p timeout=%p\n",
        (void *)port, port_attributes, flags, security, message, length ? (unsigned long long)*length : 0,
        send_attributes, receive_attributes, (void *)timeout);
    attributes("connection", connection); attributes("client", client);
    if (security && ConvertSecurityDescriptorToStringSecurityDescriptorW(security, SDDL_REVISION_1,
        OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION, &sddl, NULL)) {
        fprintf(stderr, "Server security: %ls\n", sddl); LocalFree(sddl);
    }
    fflush(stderr);
    if (forward_simple && connection && connection->Length == sizeof(*connection) &&
        !connection->RootDirectory && !connection->Attributes && !connection->SecurityDescriptor &&
        !connection->SecurityQualityOfService && !security) {
        typedef NTSTATUS (WINAPI *connect_fn)(HANDLE *, UNICODE_STRING *, OBJECT_ATTRIBUTES *,
            void *, ULONG, PSID, void *, SIZE_T *, void *, void *, LARGE_INTEGER *);
        connect_fn connect_port = (void *)GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "NtAlpcConnectPort");
        if (connect_port) return connect_port(port, connection->ObjectName, client, port_attributes,
            flags, NULL, message, length, send_attributes, receive_attributes, timeout);
    }
    return (NTSTATUS)0xc0000002;
}

static BOOL trace_import(HMODULE module)
{
    BYTE *base = (BYTE *)module;
    IMAGE_DOS_HEADER *dos = (void *)base;
    IMAGE_NT_HEADERS64 *nt = (void *)(base + dos->e_lfanew);
    IMAGE_IMPORT_DESCRIPTOR *imports = (void *)(base + nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress);
    for (; imports->Name; ++imports) {
        IMAGE_THUNK_DATA64 *names = (void *)(base + imports->OriginalFirstThunk);
        IMAGE_THUNK_DATA64 *slots = (void *)(base + imports->FirstThunk);
        if (!imports->OriginalFirstThunk) continue;
        for (; names->u1.AddressOfData; ++names, ++slots) {
            IMAGE_IMPORT_BY_NAME *name;
            DWORD old, ignored;
            if (IMAGE_SNAP_BY_ORDINAL64(names->u1.Ordinal)) continue;
            name = (void *)(base + names->u1.AddressOfData);
            if (strcmp((char *)name->Name, "NtAlpcConnectPortEx")) continue;
            if (!VirtualProtect(&slots->u1.Function, sizeof(void *), PAGE_READWRITE, &old)) return FALSE;
            slots->u1.Function = (ULONG_PTR)trace_connect;
            return VirtualProtect(&slots->u1.Function, sizeof(void *), old, &ignored);
        }
    }
    return FALSE;
}

int wmain(int argc, WCHAR **argv)
{
    HMODULE module;
    IUnknown *session = NULL;
    HRESULT (WINAPI *create)(IUnknown **);
    HRESULT (WINAPI *uninitialize)(void) = NULL;
    HRESULT hr;
    BOOL xp = FALSE;
    int i;
    if (argc < 2) { fprintf(stderr, "Usage: coreui_probe.exe NATIVE_DLL [--trace-connect|--forward-simple] [--test-host] [--test-service]\n"); return 2; }
    setbuf(stderr, NULL);
    hr = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    if (FAILED(hr)) return 1;
    module = LoadLibraryExW(argv[1], NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!module) { fprintf(stderr, "Load failed: %lu\n", GetLastError()); return 1; }
    for (i = 2; i < argc; ++i) {
        if (!wcscmp(argv[i], L"--trace-connect") || !wcscmp(argv[i], L"--forward-simple")) {
            forward_simple = !wcscmp(argv[i], L"--forward-simple");
            if (!trace_import(module)) { fprintf(stderr, "Import hook failed\n"); return 1; }
        } else if (!wcscmp(argv[i], L"--test-host")) {
            HRESULT (WINAPI *configure)(void) = (void *)GetProcAddress(module, "CoreUIConfigureTestHost");
            if (!configure) return 1;
            fprintf(stderr, "CoreUIConfigureTestHost: %08lx\n", (ULONG)configure());
        } else if (!wcscmp(argv[i], L"--test-service")) {
            // Signatures verified in native DLL/PDB. A unique test registrar is
            // local to this process; do not create/alter a system service.
            GUID id;
            HRESULT (WINAPI *initialize)(INT, const GUID *, const GUID *) = (void *)GetProcAddress(module, "CoreUIInitializeTestService");
            HRESULT (WINAPI *route)(const GUID *, const GUID *) = (void *)GetProcAddress(module, "CoreUIRouteToTestRegistrar");
            uninitialize = (void *)GetProcAddress(module, "CoreUIUninitializeTestService");
            if (!initialize || !route || !uninitialize || FAILED(CoCreateGuid(&id))) return 1;
            fprintf(stderr, "CoreUIRouteToTestRegistrar: %08lx\n", (ULONG)route(&id, NULL));
            fprintf(stderr, "CoreUIInitializeTestService: %08lx\n", (ULONG)initialize(2, &id, NULL));
        } else if (!wcscmp(argv[i], L"--xp")) xp = TRUE;
        else return 2;
    }
    if (xp) {
        HRESULT (WINAPI *create_session)(UINT32, IUnknown **) = (void *)GetProcAddress(module, "CoreMsgCreateSession");
        if (!create_session) return 1;
        hr = create_session(0, &session);
        fprintf(stderr, "CoreMsgCreateSession(0): %08lx, session=%p\n", (ULONG)hr, (void *)session);
    } else {
        create = (void *)GetProcAddress(module, "CoreUICreate");
        if (!create) return 1;
        hr = create(NULL); fprintf(stderr, "CoreUICreate(NULL): %08lx\n", (ULONG)hr);
        hr = create(&session); fprintf(stderr, "CoreUICreate: %08lx, session=%p\n", (ULONG)hr, (void *)session);
    }
    if (session) {
        const IID iid = {0x4b2edab9,0x129b,0x4733,{0xbe,0x15,0x35,0x68,0x53,0xd5,0x5a,0xce}};
        IUnknown *identity = NULL, *registrar = NULL;
        HRESULT (WINAPI *get_registrar)(IUnknown *, IUnknown **);
        hr = IUnknown_QueryInterface(session, &iid, (void **)&identity);
        fprintf(stderr, "QI IMessageSession: %08lx, identity=%p\n", (ULONG)hr, (void *)identity);
        if (identity) IUnknown_Release(identity);
        get_registrar = (void *)((void **)(session->lpVtbl))[3];
        hr = get_registrar(session, &registrar);
        fprintf(stderr, "GetMessageRegistrar: %08lx, registrar=%p\n", (ULONG)hr, (void *)registrar);
        if (registrar) IUnknown_Release(registrar);
    }
    if (session) IUnknown_Release(session);
    if (uninitialize) fprintf(stderr, "CoreUIUninitializeTestService: %08lx\n", (ULONG)uninitialize());
    CoUninitialize();
    return FAILED(hr);
}
