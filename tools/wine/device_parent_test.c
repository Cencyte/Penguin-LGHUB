/* Exercise parent lookup without opening or changing physical hardware.
 * Creates temporary SetupAPI nodes in the test prefix; removes them before exit.
 * Build: x86_64-w64-mingw32-gcc -Wall -Wextra -Werror device_parent_test.c
 *        -o device_parent_test.exe -lsetupapi -lcfgmgr32
 */
#include <windows.h>
#include <setupapi.h>
#include <cfgmgr32.h>
#include <initguid.h>
#include <devpkey.h>
#include <stdio.h>

/* The pinned MinGW header omits this export. */
WINSETUPAPI BOOL WINAPI SetupDiSetDevicePropertyW(HDEVINFO, PSP_DEVINFO_DATA,
    const DEVPROPKEY *, DEVPROPTYPE, const BYTE *, DWORD, DWORD);

static unsigned failures;
#define CHECK(condition) do { if (!(condition)) { \
    fprintf(stderr, "FAIL line %u: %s (error %lu)\n", __LINE__, #condition, GetLastError()); \
    failures++; } } while (0)

int main(void)
{
    HDEVINFO set = SetupDiCreateDeviceInfoList(NULL, NULL);
    SP_DEVINFO_DATA parent = {.cbSize = sizeof(parent)};
    SP_DEVINFO_DATA child = {.cbSize = sizeof(child)};
    GUID guid = {0};
    WCHAR parent_id[MAX_DEVICE_ID_LEN], child_id[MAX_DEVICE_ID_LEN], actual[MAX_DEVICE_ID_LEN];
    DEVINST result = 0;
    if (set == INVALID_HANDLE_VALUE) return 1;
    swprintf(parent_id, MAX_DEVICE_ID_LEN, L"ROOT\\GHUB_PARENT_TEST\\%lu", GetCurrentProcessId());
    swprintf(child_id, MAX_DEVICE_ID_LEN, L"ROOT\\GHUB_CHILD_TEST\\%lu", GetCurrentProcessId());
    CHECK(SetupDiCreateDeviceInfoW(set, parent_id, &guid, NULL, NULL, 0, &parent));
    CHECK(SetupDiCreateDeviceInfoW(set, child_id, &guid, NULL, NULL, 0, &child));
    if (failures) goto done;
    CHECK(SetupDiRegisterDeviceInfo(set, &parent, 0, NULL, NULL, NULL));
    CHECK(SetupDiRegisterDeviceInfo(set, &child, 0, NULL, NULL, NULL));
    if (failures) goto done;

    CHECK(CM_Get_Parent(NULL, child.DevInst, 0) == CR_INVALID_POINTER);
    CHECK(CM_Get_Parent(&result, child.DevInst, 1) == CR_INVALID_FLAG);
    CHECK(CM_Get_Parent(&result, (DEVINST)-1, 0) == CR_NO_SUCH_DEVNODE);
    CHECK(CM_Get_Parent(&result, child.DevInst, 0) == CR_NO_SUCH_DEVNODE);
    CHECK(SetupDiSetDevicePropertyW(set, &child, &DEVPKEY_Device_Parent,
        DEVPROP_TYPE_STRING, (BYTE *)parent_id, (lstrlenW(parent_id) + 1) * sizeof(WCHAR), 0));
    CHECK(CM_Get_Parent(&result, child.DevInst, 0) == CR_SUCCESS);
    CHECK(result == parent.DevInst);
    CHECK(CM_Get_Device_IDW(result, actual, MAX_DEVICE_ID_LEN, 0) == CR_SUCCESS);
    CHECK(!lstrcmpiW(actual, parent_id));
    /* Reopening a device must not change its parent identity. */
    CHECK(CM_Get_Parent(&result, child.DevInst, 0) == CR_SUCCESS);
    CHECK(result == parent.DevInst);

done:
    if (child.Reserved) CHECK(SetupDiRemoveDevice(set, &child));
    if (parent.Reserved) CHECK(SetupDiRemoveDevice(set, &parent));
    CHECK(SetupDiDestroyDeviceInfoList(set));
    printf("device parent regression: %s (%u failures)\n", failures ? "FAIL" : "PASS", failures);
    return failures ? 1 : 0;
}
