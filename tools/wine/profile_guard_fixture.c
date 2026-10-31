/* Run as lghub_agent.exe in a disposable directory. The adapter must refuse
 * this wrong executable hash before invoking any pinned internal address. */
#include <windows.h>
int main(void)
{
    if (!LoadLibraryW(L"mfsensorgroup.dll")) return 2;
    Sleep(1500);
    return 0;
}
