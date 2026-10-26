/* EnqueueSetEvent: GPU ordering, event rights/semantics and independent lifetime.
 * Run in a stopped laboratory prefix on private Xvfb with the matched DLL trio. */
#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <cstdio>

static unsigned checks, failures;
#define CHECK(x)                                                                                                  \
    do                                                                                                            \
    {                                                                                                             \
        ++checks;                                                                                                 \
        if (!(x))                                                                                                 \
        {                                                                                                         \
            ++failures;                                                                                           \
            std::printf("FAIL %d: %s\n", __LINE__, #x);                                                           \
        }                                                                                                         \
    } while (0)
#define HR(x)                                                                                                     \
    do                                                                                                            \
    {                                                                                                             \
        HRESULT result_ = (x);                                                                                    \
        CHECK(SUCCEEDED(result_));                                                                                \
        if (FAILED(result_))                                                                                      \
        {                                                                                                         \
            std::printf("HRESULT %08lx: %s\n", result_, #x);                                                      \
            return;                                                                                               \
        }                                                                                                         \
    } while (0)

template <class T> struct Ref
{
    T *p = nullptr;
    ~Ref()
    {
        reset();
    }
    Ref() = default;
    Ref(const Ref &) = delete;
    Ref &operator=(const Ref &) = delete;
    T *operator->() const
    {
        return p;
    }
    void reset()
    {
        if (p)
            p->Release();
        p = nullptr;
    }
};
struct Handle
{
    HANDLE h = nullptr;
    ~Handle()
    {
        if (h)
            CloseHandle(h);
    }
};
struct Device
{
    Ref<ID3D11Device> device;
    Ref<ID3D11DeviceContext> context;
    Ref<IDXGIDevice2> dxgi;
    bool init()
    {
        HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0, nullptr, 0,
                                       D3D11_SDK_VERSION, &device.p, nullptr, &context.p);
        CHECK(SUCCEEDED(hr));
        if (FAILED(hr))
            return false;
        hr = device->QueryInterface(__uuidof(IDXGIDevice2), reinterpret_cast<void **>(&dxgi.p));
        CHECK(SUCCEEDED(hr));
        return SUCCEEDED(hr);
    }
};
static bool wait(HANDLE event)
{
    DWORD result = WaitForSingleObject(event, 10000);
    CHECK(result == WAIT_OBJECT_0);
    return result == WAIT_OBJECT_0;
}

static void semantics()
{
    Device d;
    if (!d.init())
        return;
    Handle manual, automatic, modify, synchronize, zero, mutex, semaphore;
    manual.h = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    automatic.h = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    CHECK(manual.h && automatic.h);
    // This must progress while the application makes no further D3D calls.
    HR(d.dxgi->EnqueueSetEvent(manual.h));
    if (!wait(manual.h))
        return;
    CHECK(WaitForSingleObject(manual.h, 0) == WAIT_OBJECT_0);
    HR(d.dxgi->EnqueueSetEvent(automatic.h));
    if (!wait(automatic.h))
        return;
    CHECK(WaitForSingleObject(automatic.h, 0) == WAIT_TIMEOUT);
    for (unsigned i = 0; i < 8; ++i)
    {
        CHECK(ResetEvent(manual.h));
        HR(d.dxgi->EnqueueSetEvent(manual.h));
        if (!wait(manual.h))
            return;
    }
    // An already-signaled event is never reset by enqueueing another signal.
    HR(d.dxgi->EnqueueSetEvent(manual.h));
    CHECK(WaitForSingleObject(manual.h, 0) == WAIT_OBJECT_0);
    CHECK(DuplicateHandle(GetCurrentProcess(), manual.h, GetCurrentProcess(), &modify.h, EVENT_MODIFY_STATE, FALSE,
                          0));
    CHECK(DuplicateHandle(GetCurrentProcess(), manual.h, GetCurrentProcess(), &synchronize.h, SYNCHRONIZE, FALSE,
                          0));
    CHECK(DuplicateHandle(GetCurrentProcess(), manual.h, GetCurrentProcess(), &zero.h, 0, FALSE, 0));
    // Drain the earlier signal before resetting for the rights-only check.
    HR(d.dxgi->EnqueueSetEvent(automatic.h));
    if (!wait(automatic.h))
        return;
    CHECK(ResetEvent(manual.h));
    HR(d.dxgi->EnqueueSetEvent(modify.h));
    if (!wait(manual.h))
        return;
    CHECK(ResetEvent(manual.h));
    CHECK(d.dxgi->EnqueueSetEvent(synchronize.h) == E_INVALIDARG);
    CHECK(d.dxgi->EnqueueSetEvent(zero.h) == E_INVALIDARG);
    CHECK(WaitForSingleObject(manual.h, 0) == WAIT_TIMEOUT);
    CHECK(d.dxgi->EnqueueSetEvent(nullptr) == E_INVALIDARG);
    CHECK(d.dxgi->EnqueueSetEvent(INVALID_HANDLE_VALUE) == E_INVALIDARG);
    mutex.h = CreateMutexW(nullptr, FALSE, nullptr);
    semaphore.h = CreateSemaphoreW(nullptr, 0, 1, nullptr);
    CHECK(d.dxgi->EnqueueSetEvent(mutex.h) == E_INVALIDARG);
    CHECK(d.dxgi->EnqueueSetEvent(semaphore.h) == E_INVALIDARG);
    CHECK(WaitForSingleObject(semaphore.h, 0) == WAIT_TIMEOUT);
    CHECK(d.dxgi->EnqueueSetEvent(GetCurrentProcess()) == E_INVALIDARG);
    HANDLE closed = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    CHECK(CloseHandle(closed));
    CHECK(d.dxgi->EnqueueSetEvent(closed) == E_INVALIDARG);
}

static void ordering_and_lifetime()
{
    Device d;
    if (!d.init())
        return;
    constexpr UINT side = 256;
    D3D11_TEXTURE2D_DESC td = {};
    td.Width = td.Height = side;
    td.MipLevels = td.ArraySize = td.SampleDesc.Count = 1;
    td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    td.BindFlags = D3D11_BIND_RENDER_TARGET;
    Ref<ID3D11Texture2D> texture, staging;
    Ref<ID3D11RenderTargetView> rt;
    HR(d.device->CreateTexture2D(&td, nullptr, &texture.p));
    HR(d.device->CreateRenderTargetView(texture.p, nullptr, &rt.p));
    td.BindFlags = 0;
    td.Usage = D3D11_USAGE_STAGING;
    td.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    HR(d.device->CreateTexture2D(&td, nullptr, &staging.p));
    Ref<ID3D11Query> query;
    D3D11_QUERY_DESC qd = {D3D11_QUERY_EVENT, 0};
    HR(d.device->CreateQuery(&qd, &query.p));
    for (unsigned iteration = 0; iteration < 6; ++iteration)
    {
        Handle event, retained, replacement;
        event.h = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        CHECK(DuplicateHandle(GetCurrentProcess(), event.h, GetCurrentProcess(), &retained.h, SYNCHRONIZE, FALSE,
                              0));
        const float red[4] = {1, 0, 0, 1}, green[4] = {0, 1, 0, 1};
        // Queue enough real GPU work to expose a premature SetEvent shortcut.
        for (unsigned i = 0; i < 512; ++i)
            d.context->ClearRenderTargetView(rt.p, i & 1 ? green : red);
        d.context->CopyResource(staging.p, texture.p);
        d.context->End(query.p);
        HR(d.dxgi->EnqueueSetEvent(event.h));
        CHECK(CloseHandle(event.h));
        event.h = nullptr;
        replacement.h = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        if (!wait(retained.h))
            return;
        // Query readiness is independent of Map, which can implicitly finish
        // Wine's CS even with DO_NOT_WAIT. No Flush/GetData polling here.
        BOOL complete = FALSE;
        CHECK(d.context->GetData(query.p, &complete, sizeof(complete), D3D11_ASYNC_GETDATA_DONOTFLUSH) == S_OK &&
              complete);
        // The copy precedes the completion event; also verify actual pixels.
        D3D11_MAPPED_SUBRESOURCE mapped = {};
        HRESULT hr = d.context->Map(staging.p, 0, D3D11_MAP_READ, D3D11_MAP_FLAG_DO_NOT_WAIT, &mapped);
        CHECK(hr == S_OK);
        if (SUCCEEDED(hr))
        {
            unsigned bad = 0;
            for (UINT y = 0; y < side; ++y)
                for (UINT x = 0; x < side; ++x)
                    if (reinterpret_cast<const UINT *>(static_cast<const BYTE *>(mapped.pData) +
                                                       y * mapped.RowPitch)[x] != 0xff00ff00)
                        ++bad;
            d.context->Unmap(staging.p, 0);
            std::printf("completion pixels %u: %u mismatches\n", iteration, bad);
            CHECK(!bad);
        }
        CHECK(WaitForSingleObject(replacement.h, 0) == WAIT_TIMEOUT);
    }
    // Submitted deferred work is also before the completion point.
    Ref<ID3D11DeviceContext> deferred;
    Ref<ID3D11CommandList> commands;
    HR(d.device->CreateDeferredContext(0, &deferred.p));
    const float blue[4] = {0, 0, 1, 1};
    deferred->ClearRenderTargetView(rt.p, blue);
    deferred->CopyResource(staging.p, texture.p);
    HR(deferred->FinishCommandList(FALSE, &commands.p));
    d.context->ExecuteCommandList(commands.p, FALSE);
    Handle event;
    event.h = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    HR(d.dxgi->EnqueueSetEvent(event.h));
    if (!wait(event.h))
        return;
    D3D11_MAPPED_SUBRESOURCE mapped = {};
    HR(d.context->Map(staging.p, 0, D3D11_MAP_READ, D3D11_MAP_FLAG_DO_NOT_WAIT, &mapped));
    CHECK(*static_cast<const UINT *>(mapped.pData) == 0xffff0000);
    d.context->Unmap(staging.p, 0);
    // Pending events must retire before device contexts are torn down.
    Handle final_event;
    final_event.h = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    for (unsigned i = 0; i < 512; ++i)
        d.context->ClearRenderTargetView(rt.p, blue);
    HR(d.dxgi->EnqueueSetEvent(final_event.h));
    commands.reset();
    deferred.reset();
    query.reset();
    rt.reset();
    staging.reset();
    texture.reset();
    d.context.reset();
    d.dxgi.reset();
    CHECK(d.device.p->Release() == 0);
    d.device.p = nullptr;
    wait(final_event.h);
}

static void pending_batch()
{
    Device d;
    if (!d.init())
        return;
    constexpr unsigned count = 128;
    HANDLE events[count] = {};
    // GetProcessHandleCount is stubbed in Wine 10.20. Query each actual event
    // instead, to detect retained duplicates rather than accepting a fake zero.
    struct BasicInformation
    {
        ULONG attributes, access, handles, pointers, paged, nonpaged;
        ULONG reserved[3], name_length, type_length, security_length;
        LARGE_INTEGER creation_time;
    } info = {};
    using QueryObject = LONG(WINAPI *)(HANDLE, ULONG, void *, ULONG, ULONG *);
    auto query = reinterpret_cast<QueryObject>(reinterpret_cast<void *>(GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "NtQueryObject")));
    CHECK(query != nullptr);
    if (!query)
        return;
    for (unsigned i = 0; i < count; ++i)
    {
        events[i] = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        CHECK(d.dxgi->EnqueueSetEvent(events[i]) == S_OK);
    }
    for (HANDLE event : events)
        wait(event);
    // A wake can race the producer's CloseHandle, so allow retirement to finish.
    for (HANDLE event : events)
    {
        LONG status = 0;
        for (unsigned i = 0; i < 100; ++i)
        {
            status = query(event, 0, &info, sizeof(info), nullptr);
            if (status || info.handles == 1)
                break;
            Sleep(1);
        }
        CHECK(!status && info.handles == 1);
        CloseHandle(event);
    }
}

int main()
{
    setbuf(stdout, nullptr);
    SetEnvironmentVariableA("GHUB_DXGI_ENQUEUE_EVENT", "1");
    semantics();
    if (!failures)
        ordering_and_lifetime();
    if (!failures)
        pending_batch();
    std::printf("EnqueueSetEvent: %u checks, %u failures\n", checks, failures);
    return failures ? 1 : 0;
}
