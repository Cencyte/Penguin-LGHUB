// Included after Ref and winerror in the isolated compositor. Native reference:
// dcomp SHA 74d91c2545c5f0ad..., SetInput RVA 1e60 -> VisualSetInputSink RVA 3520.
// The command carries an opaque sink handle. The lab owns a duplicate so that
// queued and committed assignments cannot become another object on handle reuse.
// Windows' exact validation timing is not asserted by this conservative backend.
#pragma once
struct InputBinding
{
    LONG refs = 1;
    HANDLE handle = nullptr;
    LUID id = {};
    ~InputBinding() { if (handle) CloseHandle(handle); }
    ULONG AddRef() { return InterlockedIncrement(&refs); }
    ULONG Release()
    {
        ULONG value = InterlockedDecrement(&refs);
        if (!value) delete this;
        return value;
    }
    bool same(const InputBinding &other) const
    {
        return id.LowPart == other.id.LowPart && id.HighPart == other.id.HighPart;
    }
    static HRESULT open(HANDLE source, InputBinding **out)
    {
        *out = nullptr;
        HMODULE module = GetModuleHandleW(L"win32u.dll");
        using Query = NTSTATUS(WINAPI *)(HANDLE, LUID *);
        auto query = module ? reinterpret_cast<Query>(reinterpret_cast<void *>(
            GetProcAddress(module, "NtQueryCompositionInputSinkLuid"))) : nullptr;
        if (!query) return E_NOTIMPL;
        Ref<InputBinding> input;
        input.p = new (std::nothrow) InputBinding;
        if (!input.p) return E_OUTOFMEMORY;
        if (!DuplicateHandle(GetCurrentProcess(), source, GetCurrentProcess(), &input->handle,
                             0, FALSE, DUPLICATE_SAME_ACCESS)) return winerror();
        // Query the owned duplicate, never a caller handle that may be closed
        // concurrently. The server validates both type and read access.
        NTSTATUS status = query(input->handle, &input->id);
        if (status < 0) return HRESULT_FROM_WIN32(RtlNtStatusToDosError(status));
        *out = input.detach();
        return S_OK;
    }
};
