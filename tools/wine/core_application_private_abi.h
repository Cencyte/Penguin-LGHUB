#pragma once
#include <inspectable.h>
#include <activation.h>

// x64 ABI identified from the user's twinapi.appcore.dll and its Microsoft PDB
// (3E33A0CE6AA088F5108B26A1DAF2DC8D1). Private factory vtable RVA 0x1a5ca8.
// Unknown interface pointees are represented as IInspectable, without claiming
// to implement their contracts. HWND-like parameters really are UINT32 here.
struct CoreEventToken { INT64 value; };
struct ICoreApplicationPrivate : public IInspectable {
    virtual HRESULT STDMETHODCALLTYPE get_Context(IInspectable **) = 0;
    virtual HRESULT STDMETHODCALLTYPE get_PrivateProperties(IInspectable **) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetWindowFromThreadId(UINT32, IInspectable **) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetViewFromThreadId(UINT32, IInspectable **) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetAppDisplayName(HSTRING *) = 0;
    virtual HRESULT STDMETHODCALLTYPE add_FinalUnhandledErrorDetected(IUnknown *, CoreEventToken *) = 0;
    virtual HRESULT STDMETHODCALLTYPE remove_FinalUnhandledErrorDetected(CoreEventToken) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetDispatcherFromHwnd(UINT32, IInspectable **) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsMainWindowCreated(boolean *) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetComponentSiteFromSiteId(GUID, IInspectable **) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetCurrentView(INT32, IInspectable **) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetHwndFromViewOnASTA(IInspectable *, UINT32 *) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetComponentSiteByViewInstanceId(UINT32, IInspectable **) = 0;
};
__CRT_UUID_DECL(ICoreApplicationPrivate, 0x17b0e613, 0x942a, 0x422d, 0x90, 0x4c, 0xf9, 0x0d, 0xc7, 0x1a, 0x7d, 0xae)
