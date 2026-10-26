/* Headless tests for the private DirectWrite interface used by WinUI text
 * measurement. Optional argv[1] is a renamed native DWrite DLL for differential
 * checks. Native binaries are read-only test inputs and are never distributed. */
#define COBJMACROS
#include <windows.h>
#include <initguid.h>
#include <dwrite.h>
#include <stdio.h>
#include <stddef.h>

DEFINE_GUID(IID_PrivateAnalyzer,0xb7e6163e,0x7f46,0x43b4,0x84,0xb3,0xe4,0xe6,0x24,0x9c,0x36,0x5e);
DEFINE_GUID(IID_Unsupported,0xdeadbeef,0,0,0,0,0,0,0,0,0,0);

typedef HRESULT (WINAPI *get_direction_fn)(IUnknown *, IDWriteTextAnalysisSource *, UINT32,
        UINT32, DWRITE_READING_DIRECTION *, BOOL *);
struct analyzer
{
    HMODULE module;
    IDWriteFactory *factory;
    IDWriteTextAnalyzer *public;
    IUnknown *private;
    get_direction_fn get;
};
struct source
{
    IDWriteTextAnalysisSource iface;
    const WCHAR *text;
    UINT32 size, chunk, base, calls, fail_at;
    LONG refs;
};
static unsigned checks, failures;
#define check(test, ...) do { ++checks; if (!(test)) { ++failures; printf("FAIL: "); printf(__VA_ARGS__); puts(""); } } while (0)

static HRESULT WINAPI source_qi(IDWriteTextAnalysisSource *iface, REFIID iid, void **out)
{
    *out = NULL;
    if (!IsEqualGUID(iid, &IID_IUnknown) && !IsEqualGUID(iid, &IID_IDWriteTextAnalysisSource)) return E_NOINTERFACE;
    *out = iface;
    IDWriteTextAnalysisSource_AddRef(iface);
    return S_OK;
}
static ULONG WINAPI source_addref(IDWriteTextAnalysisSource *iface) { return ++((struct source *)iface)->refs; }
static ULONG WINAPI source_release(IDWriteTextAnalysisSource *iface) { return --((struct source *)iface)->refs; }
static HRESULT WINAPI source_at(IDWriteTextAnalysisSource *iface, UINT32 position, const WCHAR **text, UINT32 *length)
{
    struct source *source = (struct source *)iface;
    ++source->calls;
    *text = NULL;
    *length = 0;
    if (position >= source->fail_at) return E_ACCESSDENIED;
    if (position < source->size)
    {
        *text = source->text + position;
        *length = source->size - position;
        if (source->chunk && *length > source->chunk) *length = source->chunk;
    }
    return S_OK;
}
static HRESULT WINAPI source_before(IDWriteTextAnalysisSource *iface, UINT32 position, const WCHAR **text, UINT32 *length)
{
    struct source *source = (struct source *)iface;
    if (position > source->size) return E_INVALIDARG;
    *text = source->text;
    *length = position;
    return S_OK;
}
static DWRITE_READING_DIRECTION WINAPI source_base(IDWriteTextAnalysisSource *iface) { return ((struct source *)iface)->base; }
static HRESULT WINAPI source_locale(IDWriteTextAnalysisSource *iface, UINT32 position, UINT32 *length, const WCHAR **locale)
{
    (void)iface; (void)position;
    *length = ~0u; *locale = L"en-US"; return S_OK;
}
static HRESULT WINAPI source_number(IDWriteTextAnalysisSource *iface, UINT32 position, UINT32 *length, IDWriteNumberSubstitution **number)
{
    (void)iface; (void)position;
    *length = ~0u; *number = NULL; return S_OK;
}
static IDWriteTextAnalysisSourceVtbl source_vtbl =
{
    source_qi, source_addref, source_release, source_at, source_before,
    source_base, source_locale, source_number
};
static struct source source = {{&source_vtbl}, NULL, 0, 0, 0, 0, ~0u, 1};

static BOOL load_analyzer(const char *path, struct analyzer *a)
{
    HRESULT (WINAPI *create)(DWRITE_FACTORY_TYPE, REFIID, IUnknown **);
    HRESULT hr;
    a->module = LoadLibraryA(path);
    check(a->module != NULL, "LoadLibrary %s: %lu", path, GetLastError());
    if (!a->module) return FALSE;
    create = (void *)GetProcAddress(a->module, "DWriteCreateFactory");
    check(create != NULL, "DWriteCreateFactory missing");
    if (!create) return FALSE;
    hr = create(DWRITE_FACTORY_TYPE_ISOLATED, &IID_IDWriteFactory, (IUnknown **)&a->factory);
    check(hr == S_OK, "factory: %08lx", (ULONG)hr);
    if (FAILED(hr)) return FALSE;
    hr = IDWriteFactory_CreateTextAnalyzer(a->factory, &a->public);
    check(hr == S_OK, "analyzer: %08lx", (ULONG)hr);
    if (FAILED(hr)) return FALSE;
    hr = IDWriteTextAnalyzer_QueryInterface(a->public, &IID_PrivateAnalyzer, (void **)&a->private);
    check(hr == S_OK, "private interface: %08lx", (ULONG)hr);
    if (FAILED(hr)) return FALSE;
    a->get = (void *)((void **)a->private->lpVtbl)[3];
    return TRUE;
}
static void unload_analyzer(struct analyzer *a)
{
    if (a->private) IUnknown_Release(a->private);
    if (a->public) IDWriteTextAnalyzer_Release(a->public);
    if (a->factory) IDWriteFactory_Release(a->factory);
    if (a->module) FreeLibrary(a->module);
}
static void compare_range(struct analyzer *a, struct analyzer *native, UINT32 start, UINT32 length,
        int expected, unsigned case_id)
{
    DWRITE_READING_DIRECTION direction = 99, native_direction = 99;
    BOOL neutral = 99, native_neutral = 99;
    HRESULT hr, native_hr;
    hr = a->get(a->private, &source.iface, start, length, &direction, &neutral);
    check(hr == S_OK, "case %u start %u length %u hr %08lx", case_id, start, length, (ULONG)hr);
    if (expected != -2)
        check(direction == (expected < 0 ? source.base : (source.base & ~1) | expected) && neutral == (expected < 0),
                "case %u base %u chunk %u: direction %u neutral %d", case_id, source.base, source.chunk, direction, neutral);
    if (native->private)
    {
        native_hr = native->get(native->private, &source.iface, start, length, &native_direction, &native_neutral);
        check(hr == native_hr && direction == native_direction && neutral == native_neutral,
                "native comparison case %u start %u length %u base %u chunk %u: Wine %08lx/%u/%d native %08lx/%u/%d",
                case_id, start, length, source.base, source.chunk, (ULONG)hr, direction, neutral,
                (ULONG)native_hr, native_direction, native_neutral);
    }
    check(source.refs == 1, "source reference leak: %ld", source.refs);
}
int main(int argc, char **argv)
{
    static const struct { const WCHAR *text; int expected; } cases[] =
    {
        {L"abc",0}, {L"\x5d0",1}, {L"\x627",1}, {L"123 .",-1}, {L"123 \x5d0 abc",1},
        {L"\nabc",-1}, {L"\t\x5d0",1}, {L"\x2029" L"abc",-1}, {L"\xd802\xdd00",1},
        {L"\xd801\xdc00",0}, {L"\xd802",0}, {L"\xdd00",0},
        {L"\x2067" L"abc\x2069\x5d0",0}, {L"\x2066\x5d0\x2069" L"abc",1},
        {L"\x202b" L"abc",0}, {L"\x200f" L"abc",1}, {L"",-1}, {L"\x301\x5d0",1},
        {L"\x200e\x5d0",0}, {L"\x61c" L"abc",1}, {L" \x661\x662!",-1},
        {L"...\xd83d\xde00\x5d0",1}, {L"\x4e00\x5d0",0}, {L"\r\x5d0",-1},
        {L"a\n\x5d0",0}, {L"\x2028\x5d0",1}, {L"\x202e" L"a",0},
        {L"\x2068\x5d0\x2069" L"a",1}, {L"\0\x5d0",-1}, {L"\xfffd\x5d0",1}
    };
    struct analyzer a = {0}, native = {0};
    IUnknown *identity = NULL, *public_identity = NULL, *private_again = NULL;
    HRESULT hr;
    unsigned i, base, chunk;
    BOOL neutral;
    DWRITE_READING_DIRECTION direction;
    if (!load_analyzer("dwrite.dll", &a)) goto done;
    if (argc > 1 && !load_analyzer(argv[1], &native)) goto done;
    hr = IUnknown_QueryInterface(a.private, &IID_IUnknown, (void **)&identity);
    check(hr == S_OK, "private IUnknown %08lx", (ULONG)hr);
    hr = IDWriteTextAnalyzer_QueryInterface(a.public, &IID_IUnknown, (void **)&public_identity);
    check(hr == S_OK && identity == public_identity, "canonical identity differs");
    if (identity) IUnknown_Release(identity);
    if (public_identity) IUnknown_Release(public_identity);
    hr = IUnknown_QueryInterface(a.private, &IID_PrivateAnalyzer, (void **)&private_again);
    check(hr == S_OK && private_again == a.private, "private interface roundtrip");
    if (private_again) IUnknown_Release(private_again);
    private_again = (void *)1;
    hr = IUnknown_QueryInterface(a.private, &IID_Unsupported, (void **)&private_again);
    check(hr == E_NOINTERFACE && !private_again, "unsupported interface accepted");
    for (i = 0; i < ARRAYSIZE(cases); ++i)
    {
        source.text = cases[i].text;
        source.size = lstrlenW(source.text);
        for (base = 0; base < 4; ++base) for (chunk = 0; chunk < 4; ++chunk)
        {
            source.base = base; source.chunk = chunk;
            compare_range(&a, &native, 0, source.size, cases[i].expected, i);
        }
    }
    /* Embedded NUL is a code unit, not the end of a counted range. */
    source.text = L"\0\x5d0"; source.size = 2;
    compare_range(&a, &native, 0, 2, 1, 30);
    /* Prefix, suffix, empty, and truncated surrogate ranges in a single source. */
    source.text = L"12 \xd802\xdd00 A\n\x5d0"; source.size = lstrlenW(source.text);
    for (chunk = 0; chunk < 4; ++chunk)
    {
        source.chunk = chunk;
        for (i = 0; i <= source.size; ++i)
            for (base = 0; base <= source.size - i; ++base)
                compare_range(&a, &native, i, base, -2, 31);
    }
    source.calls = 0;
    compare_range(&a, &native, source.size, 0, -1, 32);
    check(!source.calls, "empty range calls the text provider");
    if (native.private)
    {
        static const WCHAR alphabet[] = {L' ', L'!', L'1', L'A', 0x5d0, 0x627,
            0x301, 0x93e, 0x9be, 0xd3e, 0x200d, 0xfe0f, 0x2029, 0x2066, 0x2067, 0x2069};
        WCHAR text[5] = {0};
        unsigned j;
        source.text = text; source.size = 4;
        for (i = 0; i < ARRAYSIZE(alphabet); ++i) for (j = 0; j < ARRAYSIZE(alphabet); ++j)
        {
            text[0] = alphabet[i]; text[1] = alphabet[j]; text[2] = 0x5d0; text[3] = L'A';
            for (chunk = 0; chunk < 2; ++chunk)
            {
                source.chunk = chunk;
                compare_range(&a, &native, 0, 4, -2, 1000 + i * ARRAYSIZE(alphabet) + j);
            }
        }
    }
    source.text = L"12 \xd802\xdd00 A\n\x5d0"; source.size = lstrlenW(source.text);
    direction = 99; neutral = 99;
    hr = a.get(a.private, NULL, 0, 0, &direction, &neutral);
    check(hr == E_INVALIDARG && direction == 0 && neutral == TRUE, "NULL source %08lx", (ULONG)hr);
    hr = a.get(a.private, &source.iface, ~0u, 1, &direction, &neutral);
    check(hr == E_INVALIDARG && direction == 0 && neutral == TRUE, "overflow %08lx", (ULONG)hr);
    /* Hardened error handling: provider faults must not be converted to success.
     * These deliberately invalid sources are not sent to the native DLL. */
    source.fail_at = 0;
    hr = a.get(a.private, &source.iface, 0, 1, &direction, &neutral);
    check(hr == E_ACCESSDENIED && direction == 0 && neutral == TRUE, "provider failure %08lx", (ULONG)hr);
    source.fail_at = 1; source.chunk = 1;
    hr = a.get(a.private, &source.iface, 0, 3, &direction, &neutral);
    check(hr == E_ACCESSDENIED, "later provider failure %08lx", (ULONG)hr);
    source.fail_at = ~0u;
    hr = a.get(a.private, &source.iface, source.size, 1, &direction, &neutral);
    check(hr == E_INVALIDARG, "premature end %08lx", (ULONG)hr);
    hr = a.get(a.private, &source.iface, 0, 1, NULL, &neutral);
    check(hr == E_POINTER, "NULL direction %08lx", (ULONG)hr);
    hr = a.get(a.private, &source.iface, 0, 1, &direction, NULL);
    check(hr == E_POINTER, "NULL neutral %08lx", (ULONG)hr);
done:
    unload_analyzer(&native); unload_analyzer(&a);
    printf("%u checks, %u failures\n", checks, failures);
    return failures ? 1 : 0;
}
