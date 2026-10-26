// Private x64 Wine lab ABI (code29); not a Windows DirectComposition ABI.
#pragma once
struct InputRegion
{
    RasterRect area;
    Ref<InputBinding> sink;
    InputRegion(const RasterRect &rect, InputBinding *value) : area(rect) { sink.retain(value); }
    InputRegion(InputRegion &&) = default;
    InputRegion &operator=(InputRegion &&) = default;
};
struct InputWireRegion
{
    double left, top, right, bottom;
    UINT sink, reserved;
};
static_assert(sizeof(InputWireRegion) == 40, "server input region ABI");
struct InputParams
{
    UINT version = 0x47484931, operation;
    HANDLE object = nullptr, scene = nullptr;
    UINT count = 0, reserved = 0;
    void *regions = nullptr;
    ULONG_PTR owner = 0;
};
static_assert(sizeof(InputParams) == 48, "input bridge ABI");
static HRESULT input_call(InputParams &params, HWND window = nullptr)
{
    HMODULE module = GetModuleHandleW(L"win32u.dll");
    auto call = module ? reinterpret_cast<CompositionCall>(reinterpret_cast<void *>(
        GetProcAddress(module, "NtUserCallHwndParam"))) : nullptr;
    if (!call) return E_NOTIMPL;
    HRESULT hr = call(window, reinterpret_cast<ULONG_PTR>(&params), 29) ? S_OK : winerror();
    if (FAILED(hr) && hr != HRESULT_FROM_WIN32(ERROR_INSUFFICIENT_BUFFER) &&
            GetEnvironmentVariableA("GHUB_DCOMP_TRACE", nullptr, 0))
        std::fprintf(stderr, "dcomp input bridge: operation=%u window=%p hr=%08lx\n", params.operation, window, hr);
    return hr;
}
class InputScene
{
    LONG refs = 1;
  public:
    HANDLE handle = nullptr;
    ~InputScene() { if (handle) CloseHandle(handle); }
    ULONG AddRef() { return InterlockedIncrement(&refs); }
    ULONG Release() { ULONG n = InterlockedDecrement(&refs); if (!n) delete this; return n; }
    static HRESULT create(const std::vector<InputRegion> &regions, InputScene **out)
    {
        *out = nullptr;
        if (regions.empty()) return S_OK;
        if (regions.size() > 4096) return E_INVALIDARG;
        Ref<InputScene> scene;
        scene.p = new InputScene;
        std::vector<InputWireRegion> wire;
        wire.reserve(regions.size());
        for (const auto &r : regions)
            wire.push_back({r.area.left, r.area.top, r.area.right, r.area.bottom,
                            static_cast<UINT>(reinterpret_cast<ULONG_PTR>(r.sink->handle)), 0});
        InputParams p{0x47484931, 1};
        p.count = wire.size(); p.regions = wire.data();
        HRESULT hr = input_call(p);
        if (FAILED(hr)) return hr;
        scene->handle = p.scene;
        if (GetEnvironmentVariableA("GHUB_DCOMP_TRACE", nullptr, 0))
            std::fprintf(stderr, "dcomp input scene: regions=%zu handle=%p\n", regions.size(), p.scene);
        *out = scene.detach();
        return S_OK;
    }
    static HRESULT get(HANDLE section, InputScene **out)
    {
        *out = nullptr;
        Ref<InputScene> scene;
        scene.p = new InputScene;
        InputParams p{0x47484931, 4}; p.object = section;
        HRESULT hr = input_call(p);
        if (FAILED(hr)) return hr;
        scene->handle = p.scene;
        if (p.scene) *out = scene.detach();
        return S_OK;
    }
    static HRESULT publish(HANDLE section, InputScene *scene)
    {
        InputParams p{0x47484931, 3}; p.object = section; p.scene = scene ? scene->handle : nullptr;
        return input_call(p);
    }
    static HRESULT bind(HWND window, ULONG_PTR owner, InputScene *scene)
    {
        InputParams p{0x47484931, 5}; p.owner = owner; p.scene = scene ? scene->handle : nullptr;
        HRESULT hr = input_call(p, window);
        if (GetEnvironmentVariableA("GHUB_DCOMP_TRACE", nullptr, 0))
            std::fprintf(stderr, "dcomp input bind: window=%p scene=%p hr=%08lx\n", window, p.scene, hr);
        return hr;
    }
    HRESULT read(std::vector<InputRegion> &out)
    {
        InputParams p{0x47484931, 2}; p.scene = handle;
        HRESULT hr = input_call(p);
        if (hr != HRESULT_FROM_WIN32(ERROR_INSUFFICIENT_BUFFER) && FAILED(hr)) return hr;
        if (p.count > 4096) return E_INVALIDARG;
        struct Handles
        {
            std::vector<InputWireRegion> values;
            bool owned = false;
            ~Handles() { if (owned) for (const auto &r : values) if (r.sink)
                CloseHandle(reinterpret_cast<HANDLE>(static_cast<ULONG_PTR>(r.sink))); }
        } wire;
        wire.values.resize(p.count);
        p.regions = wire.values.data();
        hr = input_call(p);
        if (FAILED(hr)) return hr;
        wire.owned = true;
        std::vector<InputRegion> next;
        next.reserve(p.count);
        for (const auto &r : wire.values)
        {
            Ref<InputBinding> sink;
            hr = InputBinding::open(reinterpret_cast<HANDLE>(static_cast<ULONG_PTR>(r.sink)), &sink.p);
            if (FAILED(hr)) return hr;
            next.emplace_back(RasterRect{r.left, r.top, r.right, r.bottom}, sink.p);
        }
        out = std::move(next);
        return S_OK;
    }
};
