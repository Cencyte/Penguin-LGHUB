/* Exercise the adapter with the real bundled XP runtime: COM identity, native
 * port ownership, endpoint creation and message dispatch. Run only in a copied
 * prefix with matched experimental ALPC/wait components and a private display.
 * The external harness must bound hangs; an alive process is not a test pass.
 */
#define COBJMACROS
#include <windows.h>
#include <objbase.h>
#include <sddl.h>
#include <stdio.h>
#include <string.h>

typedef HRESULT (WINAPI *message_callback)(void *, const void *, INT);
static unsigned failures, delivered;
static const DWORD payload = 0x42617a31;

#define CHECK(test) do { if (!(test)) { fprintf(stderr, "FAIL line %u: %s\n", __LINE__, #test); ++failures; } } while (0)

static HRESULT WINAPI receive(void *context, const void *data, INT length)
{
    CHECK(context != NULL);
    CHECK(length == sizeof(payload));
    if (length == sizeof(payload)) CHECK(!memcmp(data, &payload, sizeof(payload)));
    ++delivered;
    fprintf(stderr, "Callback: %d bytes; stopping dispatch\n", length);
    PostQuitMessage(0);
    return S_OK;
}

int wmain(int argc, WCHAR **argv)
{
    const IID session_iid = {0x4b2edab9,0x129b,0x4733,{0xbe,0x15,0x35,0x68,0x53,0xd5,0x5a,0xce}};
    const IID unknown_iid = {0x0,0x0,0x0,{0xc0,0,0,0,0,0,0,0x46}};
    HMODULE adapter;
    HRESULT (WINAPI *create)(IUnknown **);
    HRESULT (WINAPI *create_port)(IUnknown *, void *, IUnknown **);
    HRESULT (WINAPI *create_endpoint)(IUnknown *, message_callback, void *, IUnknown *, UINT64 *);
    HRESULT (WINAPI *send)(IUnknown *, UINT64, const void *, UINT32);
    HRESULT (WINAPI *close_endpoint)(IUnknown *, UINT64);
    MSG message;
    BOOL message_result;
    IUnknown *session = NULL, *identity = NULL, *port = NULL, *port_identity = NULL;
    PSECURITY_DESCRIPTOR security = NULL;
    UINT64 endpoint = 0;
    HRESULT hr;
    setbuf(stderr, NULL);
    if (argc != 2) { fprintf(stderr, "Usage: coremessaging_adapter_test.exe ADAPTER_DLL\n"); return 2; }
    SetEnvironmentVariableW(L"GHUB_COREMSG_PORT_ADAPTER", L"1");
    SetEnvironmentVariableW(L"GHUB_COREMSG_FRAMEWORK_ADAPTER", NULL);
    SetEnvironmentVariableW(L"GHUB_COREMSG_WATCH", NULL);
    hr = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED);
    CHECK(SUCCEEDED(hr));
    if (FAILED(hr)) return 1;
    adapter = LoadLibraryExW(argv[1], NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!adapter) { fprintf(stderr, "Load failed: %lu\n", GetLastError()); return 1; }
    create = (void *)GetProcAddress(adapter, "CoreUICreate");
    CHECK(create != NULL);
    if (!create) return 1;
    CHECK(create(NULL) == E_POINTER);
    hr = create(&session);
    CHECK(SUCCEEDED(hr) && session);
    if (FAILED(hr) || !session) return 1;
    CHECK(IUnknown_QueryInterface(session, &session_iid, (void **)&identity) == S_OK);
    CHECK(identity == session);
    if (identity) IUnknown_Release(identity);
    create_port = (void *)((void **)session->lpVtbl)[8];
    create_endpoint = (void *)((void **)session->lpVtbl)[13];
    send = (void *)((void **)session->lpVtbl)[23];
    close_endpoint = (void *)((void **)session->lpVtbl)[21];
    CHECK(create_port(session, NULL, NULL) == E_POINTER);
    CHECK(ConvertStringSecurityDescriptorToSecurityDescriptorW(L"D:(A;;GA;;;WD)", SDDL_REVISION_1, &security, NULL));
    hr = create_port(session, security, &port);
    LocalFree(security);
    fprintf(stderr, "CreatePort: %08lx\n", (ULONG)hr);
    CHECK(SUCCEEDED(hr) && port);
    if (FAILED(hr) || !port) return 1;
    CHECK(IUnknown_QueryInterface(port, &unknown_iid, (void **)&port_identity) == S_OK);
    hr = create_endpoint(session, receive, session, port, &endpoint);
    fprintf(stderr, "CreateEndpoint: %08lx id=%llx\n", (ULONG)hr, endpoint);
    CHECK(SUCCEEDED(hr) && endpoint);
    if (FAILED(hr) || !endpoint) return 1;
    identity = NULL;
    CHECK(IUnknown_QueryInterface(port, &unknown_iid, (void **)&identity) == S_OK);
    CHECK(identity == port_identity);
    if (identity) IUnknown_Release(identity);
    if (port_identity) IUnknown_Release(port_identity);
    hr = send(session, endpoint, &payload, sizeof(payload));
    fprintf(stderr, "Send: %08lx\n", (ULONG)hr);
    CHECK(SUCCEEDED(hr));
    if (FAILED(hr)) return 1;
    fprintf(stderr, "Entering dispatch\n");
    /* Factory flags 0 integrates with the thread's Win32 message pump. The
     * standalone IMessageSession.Run method rejects an integrated session. */
    while ((message_result = GetMessageW(&message, NULL, 0, 0)) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    fprintf(stderr, "Dispatch returned: %d\n", message_result);
    CHECK(message_result == 0);
    CHECK(delivered == 1);
    CHECK(SUCCEEDED(close_endpoint(session, endpoint)));
    IUnknown_Release(port);
    fprintf(stderr, "Releasing session\n");
    IUnknown_Release(session);
    fprintf(stderr, "Session released\n");
    CoUninitialize();
    fprintf(stderr, "CoreMessaging adapter: %s (%u failures)\n", failures ? "FAIL" : "PASS", failures);
    return !!failures;
}
