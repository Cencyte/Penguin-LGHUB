// Isolated diagnostic for the user's Windows ninput.dll, never an application shim.
// Requires the experimental absent-handler user32 patch in the private runner.
// --null-device is the negative control for INPUT_E_PACKET.
#include <windows.h>
#include <interactioncontext.h>
#include <cstdio>
#include <cstring>

static unsigned taps;
static BOOL WINAPI device_rects(HANDLE device, RECT *device_rect, RECT *display_rect)
{
    if (device != reinterpret_cast<HANDLE>(0x47485542) || !device_rect || !display_rect)
    { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
    *device_rect = *display_rect = {0,0,1024,768};
    return TRUE;
}
// Supply only this harness's synthetic device geometry. The production API must
// derive it from an actual delivered pointer sample; this fixture does not.
static bool install_device_fixture(HMODULE module)
{
    auto *base = reinterpret_cast<BYTE *>(module);
    auto *dos = reinterpret_cast<IMAGE_DOS_HEADER *>(base);
    auto *nt = reinterpret_cast<IMAGE_NT_HEADERS *>(base+dos->e_lfanew);
    auto *entry = reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR *>(base+
        nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress);
    for (; entry->Name; ++entry)
    {
        auto *names = reinterpret_cast<IMAGE_THUNK_DATA *>(base+entry->OriginalFirstThunk);
        auto *iat = reinterpret_cast<IMAGE_THUNK_DATA *>(base+entry->FirstThunk);
        for (; names->u1.AddressOfData; ++names, ++iat)
        {
            if (IMAGE_SNAP_BY_ORDINAL(names->u1.Ordinal)) continue;
            auto *symbol = reinterpret_cast<IMAGE_IMPORT_BY_NAME *>(base+names->u1.AddressOfData);
            if (std::strcmp(symbol->Name,"GetPointerDeviceRects")) continue;
            DWORD old;
            if (!VirtualProtect(&iat->u1.Function,sizeof(iat->u1.Function),PAGE_READWRITE,&old)) return false;
            iat->u1.Function = reinterpret_cast<ULONG_PTR>(device_rects);
            return !!VirtualProtect(&iat->u1.Function,sizeof(iat->u1.Function),old,&old);
        }
    }
    return false;
}
static void CALLBACK output(void *, const INTERACTION_CONTEXT_OUTPUT *value)
{
    std::printf("OUTPUT id=%u flags=%x type=%lu xy=%g,%g taps=%u\n",value->interactionId,
        value->interactionFlags,value->inputType,value->x,value->y,value->arguments.tap.count);
    if (value->interactionId == INTERACTION_ID_TAP) ++taps;
}
#define CALL(name, ...) do { \
    auto f = reinterpret_cast<decltype(&name)>(GetProcAddress(dll,#name)); \
    if (!f) { return 2; } \
    HRESULT hr = f(__VA_ARGS__); \
    std::printf("%s hr=%08lx\n",#name,hr); \
    if (FAILED(hr)) { return 1; } \
} while (0)
int main(int argc, char **argv)
{
    if (!GetEnvironmentVariableW(L"GHUB_TRAY_PRIVATE_LAB",nullptr,0)) return 2;
    setvbuf(stdout,nullptr,_IONBF,0);
    HMODULE dll = LoadLibraryW(L"ninput_native.dll");
    if (!dll) { std::printf("Load error %lu\n",GetLastError()); return 2; }
    bool null_device = argc == 2 && !std::strcmp(argv[1],"--null-device");
    if (argc == 2 && !std::strcmp(argv[1],"--device-fixture") && !install_device_fixture(dll)) return 2;
    HINTERACTIONCONTEXT context = nullptr;
    CALL(CreateInteractionContext,&context);
    CALL(RegisterOutputCallbackInteractionContext,context,output,nullptr);
    CALL(SetPropertyInteractionContext,context,INTERACTION_CONTEXT_PROPERTY_INTERACTION_UI_FEEDBACK,FALSE);
    CALL(SetPropertyInteractionContext,context,INTERACTION_CONTEXT_PROPERTY_FILTER_POINTERS,FALSE);
    INTERACTION_CONTEXT_CONFIGURATION config = {INTERACTION_ID_TAP,INTERACTION_CONFIGURATION_FLAG_TAP};
    CALL(SetInteractionConfigurationInteractionContext,context,1,&config);
    for (UINT step = 0; step < 2; ++step)
    {
        POINTER_INFO info = {};
        info.sourceDevice = null_device ? nullptr : reinterpret_cast<HANDLE>(0x47485542);
        info.pointerType = PT_MOUSE; info.pointerId = 1; info.frameId = step+1;
        info.pointerFlags = POINTER_FLAG_PRIMARY | POINTER_FLAG_CONFIDENCE | POINTER_FLAG_INRANGE |
            (step ? POINTER_FLAG_UP : (POINTER_FLAG_DOWN|POINTER_FLAG_INCONTACT|POINTER_FLAG_FIRSTBUTTON));
        info.ptPixelLocation = info.ptPixelLocationRaw = {100,100};
        info.ptHimetricLocation = info.ptHimetricLocationRaw = {2646,2646};
        info.dwTime = GetTickCount(); info.historyCount = 1;
        LARGE_INTEGER qpc; QueryPerformanceCounter(&qpc); info.PerformanceCount = qpc.QuadPart;
        info.ButtonChangeType = step ? POINTER_CHANGE_FIRSTBUTTON_UP : POINTER_CHANGE_FIRSTBUTTON_DOWN;
        CALL(BufferPointerPacketsInteractionContext,context,1,&info);
        CALL(ProcessBufferedPacketsInteractionContext,context);
        Sleep(30);
    }
    CALL(DestroyInteractionContext,context);
    std::printf("tap callbacks=%u\n",taps);
    return taps == 1 ? 0 : 1;
}
