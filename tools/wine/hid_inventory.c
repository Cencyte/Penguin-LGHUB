/* Read-only Windows HID inventory: opens metadata handles, never reads input
 * reports or writes device configuration. Build with -lsetupapi -lhid -lcfgmgr32. */
#include <windows.h>
#include <setupapi.h>
#include <hidsdi.h>
#include <hidpi.h>
#include <cfgmgr32.h>
#include <stdio.h>
#include <stdlib.h>

int main(void)
{
    GUID guid;
    HidD_GetHidGuid(&guid);
    HDEVINFO devices = SetupDiGetClassDevsW(&guid, NULL, NULL,
                                          DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
    if (devices == INVALID_HANDLE_VALUE) return 1;
    unsigned logitech = 0;
    for (DWORD index = 0;; index++) {
        SP_DEVICE_INTERFACE_DATA iface = {.cbSize = sizeof(iface)};
        if (!SetupDiEnumDeviceInterfaces(devices, NULL, &guid, index, &iface)) {
            if (GetLastError() != ERROR_NO_MORE_ITEMS) {
                fprintf(stderr, "enumeration error %lu\n", GetLastError());
                SetupDiDestroyDeviceInfoList(devices);
                return 1;
            }
            break;
        }
        DWORD size = 0;
        SetupDiGetDeviceInterfaceDetailW(devices, &iface, NULL, 0, &size, NULL);
        PSP_DEVICE_INTERFACE_DETAIL_DATA_W detail = malloc(size);
        if (!detail) { SetupDiDestroyDeviceInfoList(devices); return 1; }
        detail->cbSize = sizeof(*detail);
        SP_DEVINFO_DATA info = {.cbSize = sizeof(info)};
        if (!SetupDiGetDeviceInterfaceDetailW(devices, &iface, detail, size, NULL, &info)) {
            fprintf(stderr, "interface detail error %lu\n", GetLastError());
            free(detail);
            continue;
        }
        HANDLE device = CreateFileW(detail->DevicePath, 0,
            FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
        free(detail);
        if (device == INVALID_HANDLE_VALUE) {
            fprintf(stderr, "metadata open error %lu\n", GetLastError());
            continue;
        }
        HIDD_ATTRIBUTES attrs = {.Size = sizeof(attrs)};
        if (HidD_GetAttributes(device, &attrs) && attrs.VendorID == 0x046d) {
            logitech++;
            WCHAR instance[MAX_DEVICE_ID_LEN] = {0};
            CM_Get_Device_IDW(info.DevInst, instance, MAX_DEVICE_ID_LEN, 0);
            printf("instance=%ls\n", instance);
            DEVINST parent = 0;
            CONFIGRET parent_status = CM_Get_Parent(&parent, info.DevInst, 0);
            WCHAR parent_name[MAX_DEVICE_ID_LEN] = {0};
            CONFIGRET id_status = parent_status == CR_SUCCESS ?
                CM_Get_Device_IDW(parent, parent_name, MAX_DEVICE_ID_LEN, 0) : parent_status;
            printf("parent status=%lu id_status=%lu id=%ls\n", parent_status, id_status, parent_name);
            WCHAR product[128] = {0};
            HidD_GetProductString(device, product, sizeof(product));
            PHIDP_PREPARSED_DATA data = NULL;
            HIDP_CAPS caps = {0};
            NTSTATUS status = 0;
            if (HidD_GetPreparsedData(device, &data)) {
                status = HidP_GetCaps(data, &caps);
                if (caps.UsagePage == 0xff00) {
                    printf("vendor caps %04x: input buttons/values=%u/%u output=%u/%u\n",
                        attrs.ProductID, caps.NumberInputButtonCaps, caps.NumberInputValueCaps,
                        caps.NumberOutputButtonCaps, caps.NumberOutputValueCaps);
                    for (int type = HidP_Input; type <= HidP_Output; type++) {
                        HIDP_VALUE_CAPS values[64];
                        USHORT count = 64;
                        NTSTATUS result = HidP_GetValueCaps(type, values, &count, data);
                        printf("  type=%d status=%08lx count=%u\n", type, (ULONG)result, count);
                        if (result == HIDP_STATUS_SUCCESS) for (USHORT k = 0; k < count; k++)
                            printf("  id=%02x bits=%u count=%u range=%u usage=%04x\n",
                                values[k].ReportID, values[k].BitSize, values[k].ReportCount,
                                values[k].IsRange, values[k].NotRange.Usage);
                    }
                }
                HidD_FreePreparsedData(data);
            }
            printf("%04x:%04x version=%04x product=%ls usage=%04x:%04x "
                   "reports(in/out/feature)=%u/%u/%u caps_status=%08lx\n",
                attrs.VendorID, attrs.ProductID, attrs.VersionNumber, product,
                caps.UsagePage, caps.Usage, caps.InputReportByteLength,
                caps.OutputReportByteLength, caps.FeatureReportByteLength, (ULONG)status);
        }
        CloseHandle(device);
    }
    SetupDiDestroyDeviceInfoList(devices);
    printf("Logitech collections: %u\n", logitech);
    return 0;
}
