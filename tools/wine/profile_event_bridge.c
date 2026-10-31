/* Pinned G HUB 2026.6 application-event adapter. No vendor code is patched.
 * Loaded by the existing agent-only mfsensorgroup compatibility DLL. The host
 * supervisor owns a private, ephemeral, token-bound atomic-file mailbox.
 */
#include <windows.h>
#include <bcrypt.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

#define PATH_LIMIT 4096
#define HEADER_SIZE 100
static const unsigned char expected_hash[32] = {
    0x61,0x77,0x2d,0xe3,0x56,0x09,0x6a,0xf7,0xce,0xa9,0x86,0xe2,0xf9,0xb3,0xf4,0x2d,
    0xb6,0x9e,0x6f,0x41,0x0c,0x41,0x06,0x60,0x99,0x98,0xc3,0x89,0xfd,0x75,0x5f,0xc1
};

static int pinned_agent(const WCHAR *path)
{
    BCRYPT_ALG_HANDLE algorithm = NULL;
    BCRYPT_HASH_HANDLE hash = NULL;
    HANDLE file = INVALID_HANDLE_VALUE;
    unsigned char bytes[65536], result[32];
    DWORD count;
    BOOL read_ok = FALSE;
    int ok = 0;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, NULL, 0) < 0) goto done;
    if (BCryptCreateHash(algorithm, &hash, NULL, 0, NULL, 0, 0) < 0) goto done;
    file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (file == INVALID_HANDLE_VALUE) goto done;
    while ((read_ok = ReadFile(file, bytes, sizeof(bytes), &count, NULL)) && count)
        if (BCryptHashData(hash, bytes, count, 0) < 0) goto done;
    if (!read_ok || BCryptFinishHash(hash, result, sizeof(result), 0) < 0) goto done;
    ok = !memcmp(result, expected_hash, sizeof(result));
done:
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    if (hash) BCryptDestroyHash(hash);
    if (algorithm) BCryptCloseAlgorithmProvider(algorithm, 0);
    return ok;
}

static uint64_t number64(const unsigned char *p)
{
    uint64_t n;
    memcpy(&n, p, sizeof(n));
    return n;
}

static uint32_t number32(const unsigned char *p)
{
    uint32_t n;
    memcpy(&n, p, sizeof(n));
    return n;
}

static uint64_t now_ms(void)
{
    FILETIME ft;
    ULARGE_INTEGER value;
    GetSystemTimeAsFileTime(&ft);
    value.LowPart = ft.dwLowDateTime;
    value.HighPart = ft.dwHighDateTime;
    return value.QuadPart / 10000 - 11644473600000ULL;
}

static void status(const WCHAR *path, const char *token, uint64_t seq, const char *message)
{
    WCHAR name[PATH_LIMIT];
    _snwprintf(name, PATH_LIMIT, L"%ls.status", path);
    FILE *f = _wfopen(name, L"wb");
    if (f) {
        fprintf(f, "%s %llu %s\n", token, (unsigned long long)seq, message);
        fclose(f);
    }
}

static void publish(BYTE *base, uint32_t id, const char *path)
{
    typedef void (*ctor_fn)(void *, void *);
    typedef void *(*app_fn)(void *);
    typedef void (*string_fn)(void *, const char *);
    typedef void (*move_fn)(void *, void *, void *, void *);
    typedef void (*send_fn)(void *, void *);
    typedef void (*dtor_fn)(void *);
    uint64_t event[6] = {0}, string[4] = {0};
    BYTE *application;
    ((ctor_fn)(base + 0x10be070))(event, NULL);
    application = ((app_fn)(base + 0x10c32f0))(NULL);
    event[4] = (uint64_t)application;
    event[5] = 3; /* logi.protocol.applications.Event.FOCUS */
    *(uint32_t *)(application + 0xdc) = id;
    /* Allocate/move/destroy with the agent's own C++ runtime, never MinGW's. */
    ((string_fn)(base + 0xedc2c0))(string, path);
    ((move_fn)(base + 0x27ed780))(application + 0x80, NULL, string, NULL);
    ((send_fn)(base + 0x1b3ac80))(base + 0x46e4860, event);
    ((dtor_fn)(base + 0x10be0a0))(event);
}

static DWORD WINAPI receive_events(void *module)
{
    WCHAR executable[PATH_LIMIT], mailbox[PATH_LIMIT];
    char token[65];
    BYTE *base = (BYTE *)GetModuleHandleW(NULL);
    HMODULE pinned;
    uint64_t last = 0;
    const WCHAR *name;
    DWORD n;
    /* Environment gate prevents activation in other importers and probes. */
    n = GetEnvironmentVariableW(L"PENGUIN_GHUB_FOCUS_FILE", mailbox, PATH_LIMIT - 16);
    if (!n || n >= PATH_LIMIT - 16) return 0;
    if (GetEnvironmentVariableA("PENGUIN_GHUB_FOCUS_TOKEN", token, sizeof(token)) != 64) return 0;
    for (n = 0; n < 64; n++)
        if (!((token[n] >= '0' && token[n] <= '9') || (token[n] >= 'a' && token[n] <= 'f'))) return 0;
    n = GetModuleFileNameW(NULL, executable, PATH_LIMIT);
    if (!n || n >= PATH_LIMIT) return 0;
    name = wcsrchr(executable, L'\\');
    if (!name || _wcsicmp(name + 1, L"lghub_agent.exe")) return 0;
    /* Keep worker code mapped even if the importer releases the sensor DLL. */
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                           (LPCWSTR)module, &pinned)) return 0;
    if (!pinned_agent(executable)) {
        status(mailbox, token, 0, "unsupported-agent");
        return 0;
    }
    status(mailbox, token, 0, "ready");
    for (;;) {
        unsigned char packet[HEADER_SIZE + PATH_LIMIT];
        HANDLE f = CreateFileW(mailbox, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                               NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
        DWORD length = 0;
        BOOL ok = FALSE;
        uint64_t seq, stamp, now;
        uint32_t id, size, flags;
        if (f != INVALID_HANDLE_VALUE) {
            ok = ReadFile(f, packet, sizeof(packet), &length, NULL);
            CloseHandle(f);
        }
        if (!ok || length < HEADER_SIZE || memcmp(packet, "PGFOCUS1", 8) || memcmp(packet + 8, token, 64)) goto next;
        seq = number64(packet + 72); stamp = number64(packet + 80);
        id = number32(packet + 88); flags = number32(packet + 92); size = number32(packet + 96);
        now = now_ms();
        if (!seq || seq <= last || stamp > now + 2000 || now > stamp + 5000 || flags > 1) goto next;
        if (flags == 1) { status(mailbox, token, seq, "stopped"); return 0; }
        if (!(id & 0x80000000u) || !size || size >= PATH_LIMIT || length != HEADER_SIZE + size) goto next;
        if (memchr(packet + HEADER_SIZE, 0, size)) goto next;
        packet[HEADER_SIZE + size] = 0;
        /* Router lifetime is established before the supervisor emits events. */
        if (!*(void **)(base + 0x46bf748) || !*(void **)(base + 0x46bf750)) goto next;
        publish(base, id, (const char *)packet + HEADER_SIZE);
        last = seq;
        status(mailbox, token, seq, "accepted");
next:
        Sleep(40);
    }
}

__declspec(dllexport) DWORD WINAPI PenguinProfileBridgeVersion(void) { return 1; }

BOOL WINAPI DllMain(HINSTANCE module, DWORD reason, void *reserved)
{
    (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        HANDLE thread;
        DisableThreadLibraryCalls(module);
        thread = CreateThread(NULL, 0, receive_events, module, 0, NULL);
        if (thread) CloseHandle(thread);
    }
    return TRUE;
}
