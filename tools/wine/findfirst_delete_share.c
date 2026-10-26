/* Reproduce G HUB's directory walk with an existing DELETE-access handle.
 * Build: x86_64-w64-mingw32-gcc -Wall -Wextra -o probe.exe this_file.c
 * Unpatched Wine 10.20: enumeration fails with ERROR_SHARING_VIOLATION (32).
 * Patched Wine: enumeration succeeds, then all test objects are removed.
 */
#include <windows.h>
#include <stdio.h>

int main(void)
{
    char temp[MAX_PATH], directory[MAX_PATH], pattern[MAX_PATH + 3];
    WIN32_FIND_DATAA data;
    HANDLE directory_handle, search;
    DWORD error;
    if (!GetTempPathA(sizeof(temp), temp) ||
        !GetTempFileNameA(temp, "ghu", 0, directory)) return 2;
    if (!DeleteFileA(directory) || !CreateDirectoryA(directory, NULL)) return 2;
    directory_handle = CreateFileA(directory, GENERIC_READ | DELETE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
        OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, NULL);
    if (directory_handle == INVALID_HANDLE_VALUE) {
        fprintf(stderr, "CreateFile: %lu\n", GetLastError());
        RemoveDirectoryA(directory);
        return 2;
    }
    snprintf(pattern, sizeof(pattern), "%s\\*", directory);
    search = FindFirstFileExA(pattern, FindExInfoBasic, &data,
        FindExSearchNameMatch, NULL, 0);
    error = search == INVALID_HANDLE_VALUE ? GetLastError() : ERROR_SUCCESS;
    if (search != INVALID_HANDLE_VALUE) FindClose(search);
    CloseHandle(directory_handle);
    if (!RemoveDirectoryA(directory)) return 2;
    printf("FindFirstFileEx with DELETE-access handle: %lu\n", error);
    return error ? 1 : 0;
}
