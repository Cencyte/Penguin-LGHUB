/* SPDX-License-Identifier: GPL-3.0-or-later
 * Acquisition-process XPRESS-Huffman bridge for Wine 10.20.
 * Microsoft's cabinet handles buffer framing; ms-compress decodes the chunks.
 * Only this process's import table changes. No Wine DLL is modified on disk.
 * Caller must verify cabinet.dll and the decoder source before launching us.
 */
static int (*xpress_decode)(const void *, size_t, void *, size_t *);
static LONG (WINAPI *original_workspace)(USHORT, ULONG *, ULONG *);
static LONG (WINAPI *original_decompress)(USHORT, UCHAR *, ULONG, UCHAR *, ULONG,
                                         ULONG *, void *);

static LONG WINAPI acquisition_workspace(USHORT format, ULONG *size, ULONG *fragment)
{
    if ((format & 0xff) != 4) return original_workspace(format, size, fragment);
    /* ms-compress manages its own workspace; cabinet still needs a valid size. */
    *size = *fragment = 1;
    return 0;
}

static LONG WINAPI acquisition_decompress(USHORT format, UCHAR *out, ULONG capacity,
                                         UCHAR *in, ULONG length, ULONG *written,
                                         void *workspace)
{
    size_t actual = capacity;
    int result;
    if ((format & 0xff) != 4)
        return original_decompress(format, out, capacity, in, length, written, workspace);
    *written = 0;
    result = xpress_decode(in, length, out, &actual);
    if (result || actual > capacity)
    {
        fprintf(stderr, "XPRESS-Huffman decoding failed: %d\n", result);
        return (LONG)0xc0000242; /* STATUS_BAD_COMPRESSION_BUFFER */
    }
    *written = (ULONG)actual;
    return 0;
}

static HMODULE load_adjacent_dll(const WCHAR *name)
{
    WCHAR path[32768], *slash;
    DWORD length = GetModuleFileNameW(NULL, path, ARRAY_SIZE(path));
    if (!length || length >= ARRAY_SIZE(path)) return NULL;
    slash = wcsrchr(path, L'\\');
    if (!slash || (size_t)(slash + 1 - path) + wcslen(name) >= ARRAY_SIZE(path)) return NULL;
    wcscpy(slash + 1, name);
    return LoadLibraryExW(path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
}

static BOOL install_xpress_backend(void)
{
    HMODULE decoder = load_adjacent_dll(L"mscompression.dll");
    HMODULE cabinet = load_adjacent_dll(L"cabinet.dll");
    BYTE *base = (BYTE *)cabinet;
    IMAGE_DOS_HEADER *dos;
    IMAGE_NT_HEADERS64 *nt;
    IMAGE_IMPORT_DESCRIPTOR *imports;
    unsigned patched = 0;
    if (!decoder || !cabinet) return FALSE;
    xpress_decode = (void *)GetProcAddress(decoder, "xpress_huff_decompress");
    if (!xpress_decode) return FALSE;
    dos = (void *)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return FALSE;
    nt = (void *)(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE || nt->FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64)
        return FALSE;
    imports = (void *)(base + nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress);
    for (; imports->Name; imports++)
    {
        IMAGE_THUNK_DATA64 *names, *iat;
        if (_stricmp((char *)base + imports->Name, "ntdll.dll")) continue;
        if (!imports->OriginalFirstThunk) return FALSE;
        names = (void *)(base + imports->OriginalFirstThunk);
        iat = (void *)(base + imports->FirstThunk);
        for (; names->u1.AddressOfData; names++, iat++)
        {
            IMAGE_IMPORT_BY_NAME *name;
            void *replacement = NULL;
            DWORD old, ignored;
            if (IMAGE_SNAP_BY_ORDINAL64(names->u1.Ordinal)) continue;
            name = (void *)(base + names->u1.AddressOfData);
            if (!strcmp((char *)name->Name, "RtlGetCompressionWorkSpaceSize"))
            {
                original_workspace = (void *)iat->u1.Function;
                replacement = acquisition_workspace;
            }
            else if (!strcmp((char *)name->Name, "RtlDecompressBufferEx"))
            {
                original_decompress = (void *)iat->u1.Function;
                replacement = acquisition_decompress;
            }
            if (!replacement) continue;
            if (!VirtualProtect(&iat->u1.Function, sizeof(void *), PAGE_READWRITE, &old)) return FALSE;
            InterlockedExchangePointer((void **)&iat->u1.Function, replacement);
            if (!VirtualProtect(&iat->u1.Function, sizeof(void *), old, &ignored)) return FALSE;
            patched++;
        }
    }
    return patched == 2;
}
