/* SPDX-License-Identifier: GPL-3.0-or-later
 * Reconstruct an acquisition candidate using Microsoft's documented ApplyDeltaB
 * interface. The caller supplies a verified UpdateCompression.dll from official
 * media; it is never embedded here. Run in a disposable, headless Wine prefix.
 * https://learn.microsoft.com/windows/win32/devnotes/msdelta-applydeltab
 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#include "xpress_acquisition_bridge.h"

typedef struct { void *data; SIZE_T size; BOOL editable; } delta_input;
typedef struct { void *data; SIZE_T size; } delta_output;
typedef BOOL (WINAPI *apply_fn)(LONGLONG, delta_input, delta_input, delta_output *);
typedef BOOL (WINAPI *free_fn)(void *);

static void *read_input(const WCHAR *path, SIZE_T *size)
{
    HANDLE file;
    LARGE_INTEGER length;
    DWORD got;
    void *data = NULL;
    *size = 0;
    file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (file == INVALID_HANDLE_VALUE) return NULL;
    if (!GetFileSizeEx(file, &length) || length.QuadPart <= 0 ||
        length.QuadPart > 32 * 1024 * 1024) goto done;
    data = malloc((size_t)length.QuadPart);
    if (!data) goto done;
    if (!ReadFile(file, data, (DWORD)length.QuadPart, &got, NULL) || got != length.QuadPart)
    {
        free(data);
        data = NULL;
        goto done;
    }
    *size = got;
done:
    CloseHandle(file);
    return data;
}

int wmain(int argc, WCHAR **argv)
{
    delta_input source = {0}, delta = {0};
    delta_output result = {0};
    HMODULE module;
    apply_fn apply;
    free_fn release;
    void *delta_allocation;
    HANDLE output;
    DWORD written;
    int status = 1;
    if (argc != 5)
    {
        fputs("usage: apply_windows_delta.exe ABSOLUTE_DLL SOURCE DELTA NEW_OUTPUT\n", stderr);
        return 2;
    }
    /* Require an explicit absolute Windows path, not DLL search-path resolution. */
    if (!((argv[1][0] && argv[1][1] == L':' && argv[1][2] == L'\\') ||
          (argv[1][0] == L'\\' && argv[1][1] == L'\\'))) return 2;
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    if (!install_xpress_backend())
    {
        fprintf(stderr, "Acquisition decompressor setup failed: %lu\n", GetLastError());
        return 1;
    }
    module = LoadLibraryExW(argv[1], NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!module)
    {
        fprintf(stderr, "LoadLibraryExW failed: %lu\n", GetLastError());
        return 1;
    }
    apply = (apply_fn)(void *)GetProcAddress(module, "ApplyDeltaB");
    release = (free_fn)(void *)GetProcAddress(module, "DeltaFree");
    source.data = read_input(argv[2], &source.size);
    delta.data = delta_allocation = read_input(argv[3], &delta.size);
    if (!apply || !release || !source.data || !delta.data) goto done;
    if (delta.size >= 8 && (!memcmp((BYTE *)delta.data + 4, "PA30", 4) ||
                            !memcmp((BYTE *)delta.data + 4, "PA31", 4)))
    {
        delta.data = (BYTE *)delta.data + 4;
        delta.size -= 4;
    }
    if (delta.size < 4 || (memcmp(delta.data, "PA30", 4) && memcmp(delta.data, "PA31", 4)))
    {
        fputs("Unsupported delta header\n", stderr);
        goto done;
    }
    if (!apply(0, source, delta, &result))
    {
        fprintf(stderr, "ApplyDeltaB failed: %lu\n", GetLastError());
        goto done;
    }
    if (!result.data || !result.size || result.size > 32 * 1024 * 1024) goto done;
    output = CreateFileW(argv[4], GENERIC_WRITE, 0, NULL, CREATE_NEW, 0, NULL);
    if (output == INVALID_HANDLE_VALUE)
    {
        fprintf(stderr, "Output creation failed: %lu\n", GetLastError());
        goto done;
    }
    if (WriteFile(output, result.data, (DWORD)result.size, &written, NULL) &&
        written == result.size && FlushFileBuffers(output)) status = 0;
    CloseHandle(output);
    if (status) DeleteFileW(argv[4]);
    else printf("Reconstructed %llu bytes; caller must verify the expected SHA-256.\n",
                (unsigned long long)result.size);
done:
    if (result.data && release) release(result.data);
    free(source.data);
    free(delta_allocation);
    FreeLibrary(module);
    return status;
}
