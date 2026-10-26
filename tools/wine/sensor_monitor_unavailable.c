/* Wine lacks Media Foundation sensor activity monitoring. G HUB tolerates its
 * failure. Satisfy the import and report unsupported monitoring honestly, without
 * copying a proprietary Windows DLL or pretending a monitor was created.
 */
#include <windows.h>

__declspec(dllexport) HRESULT WINAPI MFCreateSensorActivityMonitor(void *callback, void **monitor)
{
    if (!monitor) return E_POINTER;
    *monitor = NULL;
    if (!callback) return E_INVALIDARG;
    return E_NOTIMPL;
}
