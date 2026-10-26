/* Lab-only CoreUICreate adapter for G HUB's bundled CoreMessagingXP.dll.
 * Native Windows CoreMessaging and the bundled XP runtime expose the same
 * IMessageSession IID and all 40 methods in the same order (verified from both
 * matching Microsoft PDBs). The XP factory adds a leading flags parameter.
 * All other Wine exports retain their existing behavior through a renamed
 * copy of the installed Wine coremessaging.dll. No native Windows OS DLL is
 * required, and no objects or successful operations are fabricated here.
 */
#define COBJMACROS
#include <windows.h>
#include <unknwn.h>
#include <wchar.h>
#include <stdio.h>
#include <string.h>

static INIT_ONCE once = INIT_ONCE_STATIC_INIT;
static HRESULT (WINAPI *create_session)(UINT32, IUnknown **);
static HMODULE xp_module;
static INIT_ONCE port_once = INIT_ONCE_STATIC_INIT;
static INIT_ONCE framework_once = INIT_ONCE_STATIC_INIT;
static volatile LONG diagnostic_gate;

/* Only the inspected FrameworkUdk build is eligible. Its contract-10 adapter
 * calls IMessageSession slot 12 for CreateEndpoint; bundled CoreMessagingXP
 * implements the contract-11+ layout, where that operation is slot 13. Patch
 * this call site's member-function thunk, not the shared thunk or Wine's API
 * contract reporting. No installed executable is changed on disk.
 */
static BOOL has_pdb(HMODULE module, const GUID *guid)
{
    BYTE *base = (BYTE *)module;
    IMAGE_DOS_HEADER *dos = (void *)base;
    IMAGE_NT_HEADERS *nt;
    IMAGE_DATA_DIRECTORY directory;
    IMAGE_DEBUG_DIRECTORY *debug;
    DWORD i;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return FALSE;
    nt = (void *)(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE || nt->FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64) return FALSE;
    directory = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_DEBUG];
    if (!directory.VirtualAddress || directory.VirtualAddress > nt->OptionalHeader.SizeOfImage ||
        directory.Size > nt->OptionalHeader.SizeOfImage - directory.VirtualAddress) return FALSE;
    debug = (void *)(base + directory.VirtualAddress);
    for (i = 0; i < directory.Size / sizeof(*debug); ++i) {
        BYTE *cv;
        DWORD age;
        if (debug[i].Type != IMAGE_DEBUG_TYPE_CODEVIEW || debug[i].SizeOfData < 24 ||
            debug[i].AddressOfRawData > nt->OptionalHeader.SizeOfImage ||
            debug[i].SizeOfData > nt->OptionalHeader.SizeOfImage - debug[i].AddressOfRawData) continue;
        cv = base + debug[i].AddressOfRawData;
        memcpy(&age, cv + 20, sizeof(age));
        if (!memcmp(cv, "RSDS", 4) && !memcmp(cv + 4, guid, sizeof(*guid)) && age == 1) return TRUE;
    }
    return FALSE;
}

static BOOL CALLBACK adapt_framework(INIT_ONCE *init, void *parameter, void **context)
{
    const GUID pdb = {0xdfc7a109,0xa18a,0x6dcb,{0x56,0xfc,0x7d,0x59,0xcd,0x45,0xde,0x15}};
    const BYTE original[] = {0x48,0x8d,0x15,0xff,0xe3,0xff,0xff};
    const BYTE replacement[] = {0x48,0x8d,0x15,0x9f,0xe5,0xff,0xff};
    HMODULE framework = GetModuleHandleW(L"Microsoft.Internal.FrameworkUdk.dll");
    BYTE *call;
    DWORD old, ignored;
    (void)init; (void)parameter; (void)context;
    if (!framework || !has_pdb(framework, &pdb)) {
        SetLastError(ERROR_REVISION_MISMATCH); return FALSE;
    }
    call = (BYTE *)framework + 0x2cf1a;
    if (memcmp(call, original, sizeof(original))) {
        SetLastError(ERROR_REVISION_MISMATCH); return FALSE;
    }
    if (!VirtualProtect(call, sizeof(original), PAGE_EXECUTE_READWRITE, &old)) return FALSE;
    memcpy(call, replacement, sizeof(replacement));
    FlushInstructionCache(GetCurrentProcess(), call, sizeof(replacement));
    if (!VirtualProtect(call, sizeof(original), old, &ignored)) return FALSE;
    fprintf(stderr, "coremessaging_xp_adapter: FrameworkUdk CreateEndpoint uses slot 13\n");
    return TRUE;
}

static LONG CALLBACK trace_exception(EXCEPTION_POINTERS *exception)
{
    ULONG i;
    ULONG_PTR *stack;
    if (exception->ExceptionRecord->ExceptionCode != EXCEPTION_ACCESS_VIOLATION) return EXCEPTION_CONTINUE_SEARCH;
    fprintf(stderr, "coremsg exception rip=%llx rcx=%llx rdx=%llx r8=%llx r9=%llx\n",
        exception->ContextRecord->Rip, exception->ContextRecord->Rcx, exception->ContextRecord->Rdx,
        exception->ContextRecord->R8, exception->ContextRecord->R9);
    stack = (void *)exception->ContextRecord->Rsp;
    for (i = 0; i < 16; ++i) {
        MEMORY_BASIC_INFORMATION info;
        WCHAR path[MAX_PATH] = {0};
        if (VirtualQuery((void *)stack[i], &info, sizeof(info)) && info.Type == MEM_IMAGE &&
            GetModuleFileNameW(info.AllocationBase, path, ARRAYSIZE(path)))
            fprintf(stderr, " stack[%lu]=%llx %ls+%llx\n", i, stack[i], path, stack[i] - (ULONG_PTR)info.AllocationBase);
        else fprintf(stderr, " stack[%lu]=%llx\n", i, stack[i]);
    }
    fflush(stderr);
    return EXCEPTION_CONTINUE_SEARCH;
}

static HRESULT WINAPI create_legacy_port(IUnknown *session, void *security, IUnknown **out)
{
    const IID core_iid = {0x86b6ecf5,0xbf9a,0x424e,{0xa0,0x15,0xb0,0xbc,0xda,0xbf,0x1a,0x3c}};
    const GUID unnamed = {0};
    IUnknown *core = NULL;
    HRESULT (WINAPI *create_port)(IUnknown *, void *, const GUID *, IUnknown **);
    HRESULT hr;
    if (!out) return E_POINTER;
    *out = NULL;
    hr = IUnknown_QueryInterface(session, &core_iid, (void **)&core);
    if (FAILED(hr)) return hr;
    create_port = (void *)((void **)core->lpVtbl)[3];
    hr = create_port(core, security, &unnamed, out);
    IUnknown_Release(core);
    fprintf(stderr, "coremessaging_xp_adapter: CreatePort hr=%08lx port=%p\n", (ULONG)hr, (void *)*out);
    if (SUCCEEDED(hr) && *out && GetEnvironmentVariableW(L"GHUB_COREMSG_TRACE", NULL, 0)) {
        unsigned int i;
        for (i = 0; i < 7; ++i) fprintf(stderr, " port[%u]=%p\n", i, ((void **)(*out)->lpVtbl)[i]);
    }
    if (SUCCEEDED(hr) && *out && GetEnvironmentVariableW(L"GHUB_COREMSG_WATCH", NULL, 0)) {
        fprintf(stderr, "coremsg watch: object=%p gate=%p\n", (void *)*out, (void *)&diagnostic_gate);
        fflush(stderr);
        while (!diagnostic_gate) Sleep(50);
    }
    return hr;
}

static BOOL CALLBACK adapt_port(INIT_ONCE *init, void *parameter, void **context)
{
    const GUID pdb = {0x31554c57,0xd82a,0x7e31,{0x87,0x84,0x85,0xd5,0x5e,0x2b,0x57,0x8d}};
    IUnknown *session = parameter;
    void **vtable = (void **)session->lpVtbl;
    DWORD old, ignored;
    (void)init; (void)context;
    // Diagnostic translation of one legacy method, not a fabricated session.
    // Keep native IUnknown identity and lifetime unchanged. Guard the exact
    // inspected build before modifying its process-private vtable (never disk).
    if (!has_pdb(xp_module, &pdb) ||
        (BYTE *)create_session != (BYTE *)xp_module + 0xce80 ||
        (BYTE *)vtable[8] != (BYTE *)xp_module + 0x42ab0) {
        SetLastError(ERROR_REVISION_MISMATCH); return FALSE;
    }
    if (!VirtualProtect(vtable + 8, sizeof(void *), PAGE_READWRITE, &old)) return FALSE;
    InterlockedExchangePointer(vtable + 8, (void *)create_legacy_port);
    if (!VirtualProtect(vtable + 8, sizeof(void *), old, &ignored)) return FALSE;
    return TRUE;
}

static BOOL CALLBACK initialize(INIT_ONCE *init, void *parameter, void **context)
{
    HMODULE self, module;
    WCHAR path[32768], *separator;
    DWORD length;
    (void)init; (void)parameter; (void)context;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        (const WCHAR *)(ULONG_PTR)initialize, &self)) return FALSE;
    length = GetModuleFileNameW(self, path, ARRAYSIZE(path));
    if (!length || length == ARRAYSIZE(path)) return FALSE;
    separator = wcsrchr(path, L'\\');
    if (!separator || (SIZE_T)(separator - path) + 22 >= ARRAYSIZE(path)) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER); return FALSE;
    }
    wcscpy(separator + 1, L"CoreMessagingXP.dll");
    module = LoadLibraryExW(path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!module) return FALSE;
    create_session = (void *)GetProcAddress(module, "CoreMsgCreateSession");
    if (!create_session) {
        FreeLibrary(module); SetLastError(ERROR_PROC_NOT_FOUND); return FALSE;
    }
    xp_module = module;
    if (GetEnvironmentVariableW(L"GHUB_COREMSG_TRACE", NULL, 0)) AddVectoredExceptionHandler(1, trace_exception);
    // Session objects own code/data in this DLL; keep it mapped until exit.
    return TRUE;
}

HRESULT WINAPI CoreUICreate(IUnknown **out)
{
    HRESULT hr;
    if (!out) return E_POINTER;
    *out = NULL;
    if (!InitOnceExecuteOnce(&once, initialize, NULL, NULL)) return HRESULT_FROM_WIN32(GetLastError());
    if (GetEnvironmentVariableW(L"GHUB_COREMSG_FRAMEWORK_ADAPTER", NULL, 0) &&
        !InitOnceExecuteOnce(&framework_once, adapt_framework, NULL, NULL))
        return HRESULT_FROM_WIN32(GetLastError());
    hr = create_session(0, out);
    if (SUCCEEDED(hr) && *out && GetEnvironmentVariableW(L"GHUB_COREMSG_PORT_ADAPTER", NULL, 0)) {
        if (!InitOnceExecuteOnce(&port_once, adapt_port, *out, NULL)) {
            hr = HRESULT_FROM_WIN32(GetLastError());
            IUnknown_Release(*out); *out = NULL;
        }
    }
    fprintf(stderr, "coremessaging_xp_adapter: CoreUICreate hr=%08lx session=%p\n", (ULONG)hr, (void *)*out);
    return hr;
}

// A patched native vtable can point here until process exit.
HRESULT WINAPI DllCanUnloadNow(void) { return S_FALSE; }
