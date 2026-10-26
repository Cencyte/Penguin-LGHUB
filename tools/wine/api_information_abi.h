#pragma once
#include <inspectable.h>
#include <activation.h>

/* The IApiInformationStatics ABI from Windows.Foundation.Metadata. Keeping this
 * small declaration avoids a duplicate boolean/BYTE template specialization in
 * this workstation's pinned MinGW windows.foundation.h. No SDK is modified.
 */
struct IApiInformationStatics : public IInspectable {
    virtual HRESULT STDMETHODCALLTYPE IsTypePresent(HSTRING, boolean *) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsMethodPresent(HSTRING, HSTRING, boolean *) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsMethodPresentWithArity(HSTRING, HSTRING, UINT32, boolean *) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsEventPresent(HSTRING, HSTRING, boolean *) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsPropertyPresent(HSTRING, HSTRING, boolean *) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsReadOnlyPropertyPresent(HSTRING, HSTRING, boolean *) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsWriteablePropertyPresent(HSTRING, HSTRING, boolean *) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsEnumNamedValuePresent(HSTRING, HSTRING, boolean *) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsApiContractPresentByMajor(HSTRING, UINT16, boolean *) = 0;
    virtual HRESULT STDMETHODCALLTYPE IsApiContractPresentByMajorAndMinor(HSTRING, UINT16, UINT16, boolean *) = 0;
};
__CRT_UUID_DECL(IApiInformationStatics, 0x997439fe, 0xf681, 0x4a11, 0xb4, 0x16, 0xc1, 0x3a, 0x47, 0xe8, 0xba, 0x36)
