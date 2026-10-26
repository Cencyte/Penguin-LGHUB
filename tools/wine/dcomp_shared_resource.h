/* Private lab wire format, not the Windows kernel's composition-object format.
 * An anonymous section carries committed BGRA pixels and a GUID identifies its
 * interprocess mutex. Version 2 additionally requires the Section-owned input scene.
 * Old pixel-only consumers must reject it rather than silently discard input.
 * DuplicateHandle/CloseHandle give the storage real kernel lifetime; no process pointers or permanently retained handle registry.
 */
#pragma once

struct SharedFrame
{
    RECT area = {};
    std::vector<BYTE> pixels;
    Ref<InputScene> input_scene;
    std::vector<InputRegion> inputs;
};

class SharedResource
{
    struct Header
    {
        DWORD magic[4];
        DWORD version, capacity;
        GUID identity;
        GUID publisher;
        DWORD publisher_pid;
        DWORD reserved;
        volatile LONG64 generation;
        RECT area;
    };
    static constexpr DWORD max_side = 4096;
    static constexpr DWORD capacity = max_side * max_side * 4;
    static constexpr DWORD signature[4] = {0x42485547, 0x52414853, 0x49565245, 0x314c4153};
    LONG refs = 1;
    HANDLE section = nullptr, mutex = nullptr;
    Header *view = nullptr;
    GUID identity = {};

    struct Guard
    {
        HANDLE mutex;
        DWORD result;
        explicit Guard(HANDLE value) : mutex(value), result(WaitForSingleObject(value, 5000)) {}
        ~Guard()
        {
            if (result == WAIT_OBJECT_0 || result == WAIT_ABANDONED)
                ReleaseMutex(mutex);
        }
        HRESULT status() const
        {
            // A dead publisher may have stopped halfway through a frame copy.
            // Do not expose that frame as a completed publication.
            if (result == WAIT_ABANDONED)
                return HRESULT_FROM_WIN32(ERROR_ABANDONED_WAIT_0);
            return result == WAIT_OBJECT_0 ? S_OK : HRESULT_FROM_WIN32(ERROR_TIMEOUT);
        }
    };
    SharedResource() { InterlockedIncrement(&objects); }
    ~SharedResource()
    {
        if (view) UnmapViewOfFile(view);
        if (mutex) CloseHandle(mutex);
        if (section) CloseHandle(section);
        InterlockedDecrement(&objects);
    }
    bool valid() const
    {
        return !memcmp(view->magic, signature, sizeof(signature)) && view->version == 2 &&
               view->capacity == capacity && view->identity == identity;
    }
    static HRESULT frame_size(const RECT &area, size_t &bytes)
    {
        const LONGLONG w = static_cast<LONGLONG>(area.right) - area.left;
        const LONGLONG h = static_cast<LONGLONG>(area.bottom) - area.top;
        if (w < 0 || h < 0 || w > max_side || h > max_side ||
            area.left < -129000000 || area.top < -129000000 ||
            area.right > 129000000 || area.bottom > 129000000)
            return E_INVALIDARG;
        bytes = static_cast<size_t>(w * h * 4);
        return S_OK;
    }

  public:
    ULONG AddRef() { return InterlockedIncrement(&refs); }
    ULONG Release()
    {
        ULONG value = InterlockedDecrement(&refs);
        if (!value) delete this;
        return value;
    }
    bool same(const SharedResource *other) const { return other && identity == other->identity; }
    static HRESULT create(HANDLE *out)
    {
        if (!out) return E_POINTER;
        *out = nullptr;
        HANDLE handle = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0,
                                          sizeof(Header) + capacity, nullptr);
        if (!handle) return winerror();
        auto *header = static_cast<Header *>(MapViewOfFile(handle, FILE_MAP_WRITE, 0, 0, sizeof(Header)));
        if (!header) { HRESULT hr = winerror(); CloseHandle(handle); return hr; }
        HRESULT hr = CoCreateGuid(&header->identity);
        if (SUCCEEDED(hr))
        {
            memcpy(header->magic, signature, sizeof(signature));
            header->version = 2;
            header->capacity = capacity;
            *out = handle;
        }
        UnmapViewOfFile(header);
        if (FAILED(hr)) CloseHandle(handle);
        return hr;
    }
    static HRESULT open(HANDLE handle, SharedResource **out)
    {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (!handle || handle == INVALID_HANDLE_VALUE) return E_HANDLE;
        auto *resource = new (std::nothrow) SharedResource;
        if (!resource) return E_OUTOFMEMORY;
        HRESULT hr = E_HANDLE;
        if (DuplicateHandle(GetCurrentProcess(), handle, GetCurrentProcess(), &resource->section,
                            0, FALSE, DUPLICATE_SAME_ACCESS))
        {
            resource->view = static_cast<Header *>(MapViewOfFile(resource->section, FILE_MAP_ALL_ACCESS,
                                                               0, 0, sizeof(Header) + capacity));
            if (resource->view)
            {
                resource->identity = resource->view->identity;
                if (resource->valid())
                {
                    WCHAR guid[40], name[80];
                    StringFromGUID2(resource->identity, guid, 40);
                    swprintf(name, 80, L"Local\\GHubDCompShared-%ls", guid);
                    resource->mutex = CreateMutexW(nullptr, FALSE, name);
                    if (resource->mutex) hr = S_OK;
                    else hr = winerror();
                }
            }
        }
        if (FAILED(hr)) resource->Release();
        else *out = resource;
        return hr;
    }
    HRESULT read(SharedFrame &frame)
    {
        Guard lock(mutex);
        if (FAILED(lock.status())) return lock.status();
        if (!valid() || (view->generation & 1)) return E_FAIL;
        size_t bytes;
        HRESULT hr = frame_size(view->area, bytes);
        if (FAILED(hr)) return hr;
        SharedFrame next;
        next.area = view->area;
        const BYTE *pixels = reinterpret_cast<const BYTE *>(view + 1);
        next.pixels.assign(pixels, pixels + bytes);
        Ref<InputScene> scene;
        hr = InputScene::get(section, &scene.p);
        if (FAILED(hr)) return hr;
        std::vector<InputRegion> inputs;
        if (scene.p && FAILED(hr = scene->read(inputs))) return hr;
        next.input_scene = std::move(scene);
        next.inputs = std::move(inputs);
        frame = std::move(next);
        return S_OK;
    }
    HRESULT claim(const GUID &publisher)
    {
        Guard lock(mutex);
        if (FAILED(lock.status())) return lock.status();
        if (!valid()) return E_HANDLE;
        if (view->publisher != GUID_NULL && view->publisher != publisher)
            return HRESULT_FROM_WIN32(ERROR_BUSY);
        view->publisher = publisher;
        view->publisher_pid = GetCurrentProcessId();
        return S_OK;
    }
    void unclaim(const GUID &publisher)
    {
        Guard lock(mutex);
        if (SUCCEEDED(lock.status()) && valid() && view->publisher == publisher)
        {
            view->publisher = GUID_NULL;
            view->publisher_pid = 0;
        }
    }
    HRESULT write(const GUID &publisher, const SharedFrame &frame)
    {
        size_t bytes;
        HRESULT hr = frame_size(frame.area, bytes);
        if (FAILED(hr) || bytes != frame.pixels.size()) return E_INVALIDARG;
        Guard lock(mutex);
        if (FAILED(lock.status())) return lock.status();
        if (!valid() || view->publisher != publisher || (view->generation & 1)) return E_HANDLE;
        // The existing shared mutex serializes pixels and input metadata.
        // The Section owns the scene independently of either client process.
        if (FAILED(hr = InputScene::publish(section, frame.input_scene.p))) return hr;
        InterlockedIncrement64(&view->generation);
        if (bytes) memcpy(view + 1, frame.pixels.data(), bytes);
        view->area = frame.area;
        InterlockedIncrement64(&view->generation);
        return S_OK;
    }
    static HRESULT size(const RECT &area, size_t &bytes) { return frame_size(area, bytes); }
};
