// Private Wine 10.20 lab ABI, not a Windows DirectComposition export.
// Keep synchronized with patch0016's NtUserCallHwndParam_CompositionFrame.
#pragma once
enum { composition_call = 28, composition_version = 0x47484331 };
enum { composition_attach = 1, composition_update, composition_detach, composition_validate };
struct CompositionFrame
{
    UINT version = composition_version;
    UINT operation;
    ULONG_PTR owner;
    UINT width = 0, height = 0;
    const void *pixels = nullptr;
};
static_assert(sizeof(CompositionFrame) == 32, "x64 private composition frame ABI");
using CompositionCall = ULONG_PTR (WINAPI *)(HWND, ULONG_PTR, DWORD);
