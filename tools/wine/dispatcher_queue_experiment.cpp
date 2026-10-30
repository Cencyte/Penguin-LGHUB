/* Opt-in Windows.System dispatcher for the Wine 10.20 G HUB laboratory.
 * ABI declarations follow windows.system.idl / windows.foundation.idl. Keeping
 * them local avoids the duplicate IReference specializations in the pinned
 * MinGW C++ headers; the regression executable uses the independent C headers.
 * Current-thread queues only. Timers and dedicated threads remain explicit
 * E_NOTIMPL boundaries, not successful no-ops.
 * As required by CreateDispatcherQueueController's current-thread contract,
 * callers must pump ShutdownQueueAsync to completion before exiting the owner
 * thread. Abandonment without shutdown is not implemented by this experiment.
 */
#define INITGUID
#include <windows.h>
#include <inspectable.h>
#include <activation.h>
#include <roapi.h>
#include "mingw_agile_compat.h"
#include <winstring.h>
#include <asyncinfo.h>
#include <weakreference.h>
#include <list>
#include <vector>
#include <memory>
#include <new>
#include <cstdio>
#include <cwchar>
#include <initializer_list>

/* Absent from this pinned MinGW winerror.h; values from Wine's winerror.h. */
#define E_ILLEGAL_METHOD_CALL _HRESULT_TYPEDEF_(0x8000000e)
#define E_ILLEGAL_DELEGATE_ASSIGNMENT _HRESULT_TYPEDEF_(0x80000018)
#define RO_E_CLOSED _HRESULT_TYPEDEF_(0x80000013)

static const IID queue_iid = {0x603e88e4,0xa338,0x4ffe,{0xa4,0x57,0xa5,0xcf,0xb9,0xce,0xb8,0x99}};
static const IID queue2_iid = {0xc822c647,0x30ef,0x506e,{0xbd,0x1e,0xa6,0x47,0xae,0x66,0x75,0xff}};
static const IID statics_iid = {0xa96d83d7,0x9371,0x4517,{0x92,0x45,0xd0,0x82,0x4a,0xc1,0x2c,0x74}};
static const IID controller_iid = {0x22f34e66,0x50db,0x4e36,{0xa9,0x8d,0x61,0xc0,0x1b,0x38,0x4d,0x20}};
static const IID handler_iid = {0xdfa2dc9c,0x1a2d,0x4917,{0x98,0xf2,0x93,0x9a,0xf1,0xd6,0xe0,0xc8}};
static const IID starting_iid = {0xb58b5e24,0xe1c6,0x528e,{0x9d,0x99,0x07,0xec,0x88,0x29,0xde,0xa5}};
static const IID completed_iid = {0xfe79f855,0x2f40,0x5b88,{0xa0,0xc3,0x4c,0x04,0x2a,0x05,0xdd,0x05}};
static const IID args_iid = {0xc4724c4c,0xff97,0x40c0,{0xa2,0x26,0xcc,0x0a,0xaa,0x54,0x5e,0x89}};
static const IID deferral_iid = {0xd6269732,0x3b7f,0x46a7,{0xb4,0x0b,0x4f,0xdc,0xa2,0xa2,0xc6,0x93}};
static const IID action_iid = {0x5a648006,0x843a,0x4da9,{0x86,0x5b,0x9d,0x26,0xe5,0xdf,0xad,0x7b}};
static const IID action_handler_iid = {0xa4ed5c81,0x76c9,0x40bd,{0x8b,0xe6,0xb1,0xd9,0x0f,0xb2,0x0a,0xe7}};

struct Token { INT64 value; };
struct Handler : IUnknown { virtual HRESULT WINAPI Invoke() = 0; };
struct EventHandler : IUnknown { virtual HRESULT WINAPI Invoke(IInspectable *, IInspectable *) = 0; };
struct Action;
struct ActionHandler : IUnknown { virtual HRESULT WINAPI Invoke(Action *, AsyncStatus) = 0; };
struct Action : IInspectable {
    virtual HRESULT WINAPI put_Completed(ActionHandler *) = 0;
    virtual HRESULT WINAPI get_Completed(ActionHandler **) = 0;
    virtual HRESULT WINAPI GetResults() = 0;
};
struct DQueue : IInspectable {
    virtual HRESULT WINAPI CreateTimer(IInspectable **) = 0;
    virtual HRESULT WINAPI TryEnqueue(Handler *, boolean *) = 0;
    virtual HRESULT WINAPI TryEnqueueWithPriority(INT, Handler *, boolean *) = 0;
    virtual HRESULT WINAPI add_ShutdownStarting(EventHandler *, Token *) = 0;
    virtual HRESULT WINAPI remove_ShutdownStarting(Token) = 0;
    virtual HRESULT WINAPI add_ShutdownCompleted(EventHandler *, Token *) = 0;
    virtual HRESULT WINAPI remove_ShutdownCompleted(Token) = 0;
};
struct DQueue2 : IInspectable { virtual HRESULT WINAPI get_HasThreadAccess(boolean *) = 0; };
struct DStatics : IInspectable { virtual HRESULT WINAPI GetForCurrentThread(DQueue **) = 0; };
struct DController : IInspectable {
    virtual HRESULT WINAPI get_DispatcherQueue(DQueue **) = 0;
    virtual HRESULT WINAPI ShutdownQueueAsync(Action **) = 0;
};
struct DDeferral : IInspectable { virtual HRESULT WINAPI Complete() = 0; };
struct DArgs : IInspectable { virtual HRESULT WINAPI GetDeferral(DDeferral **) = 0; };
struct Options { DWORD size; INT thread_type; INT apartment_type; };
static_assert(sizeof(Options) == 12, "DispatcherQueueOptions ABI");

static void trace(const char *what, HRESULT hr = S_OK)
{
    if (GetEnvironmentVariableW(L"GHUB_DISPATCHER_TRACE", nullptr, 0))
        std::fprintf(stderr, "ghub-dispatcher: tid=%04lx %s hr=%08lx\n", GetCurrentThreadId(), what, hr);
}
struct Lock {
    SRWLOCK *lock;
    explicit Lock(SRWLOCK &value) : lock(&value) { AcquireSRWLockExclusive(lock); }
    ~Lock() { ReleaseSRWLockExclusive(lock); }
};
static HRESULT ids(ULONG *count, IID **out, std::initializer_list<IID> values)
{
    if (!count || !out) return E_POINTER;
    *count = 0; *out = nullptr;
    auto p = static_cast<IID *>(CoTaskMemAlloc(values.size() * sizeof(IID)));
    if (!p) return E_OUTOFMEMORY;
    for (const auto &id : values) p[(*count)++] = id;
    *out = p;
    return S_OK;
}
#define INSPECTABLE(name, ...) \
    HRESULT WINAPI GetIids(ULONG *n, IID **p) override { return ids(n, p, {__VA_ARGS__}); } \
    HRESULT WINAPI GetRuntimeClassName(HSTRING *p) override { \
        if (!p) { return E_POINTER; } return WindowsCreateString(name, ARRAYSIZE(name)-1, p); } \
    HRESULT WINAPI GetTrustLevel(TrustLevel *p) override { if (!p) return E_POINTER; *p = BaseTrust; return S_OK; }
#define REFERENCES \
    ULONG WINAPI AddRef() override { return InterlockedIncrement(&refs); } \
    ULONG WINAPI Release() override { ULONG n = InterlockedDecrement(&refs); if (!n) delete this; return n; }

/* Store agile references, not apartment-bound callback pointers. No client COM
 * calls happen under our locks; shared_ptr copies only touch this wrapper. */
struct Callback {
    IAgileReference *ref = nullptr;
    IID iid;
    ~Callback() { if (ref) ref->Release(); }
    template<class T> HRESULT resolve(T **out) { return ref->Resolve(iid, reinterpret_cast<void **>(out)); }
};
using CallbackRef = std::shared_ptr<Callback>;
static HRESULT capture(IUnknown *value, REFIID iid, CallbackRef &out)
{
    if (!value) return E_POINTER;
    try {
        auto ref = std::make_shared<Callback>();
        ref->iid = iid;
        HRESULT hr = RoGetAgileReference(AGILEREFERENCE_DEFAULT, iid, value, &ref->ref);
        if (SUCCEEDED(hr)) out = std::move(ref);
        return hr;
    } catch (const std::bad_alloc &) { return E_OUTOFMEMORY; }
}

static LONG next_action_id;
class ShutdownAction final : public Action, public IAsyncInfo {
    LONG refs = 1;
    SRWLOCK lock = SRWLOCK_INIT;
    AsyncStatus status = Started;
    HRESULT error = S_OK;
    bool assigned = false, closed = false;
    UINT32 id = InterlockedIncrement(&next_action_id);
    CallbackRef completed;
    static void invoke(const CallbackRef &ref, Action *self, AsyncStatus state) {
        if (!ref) return;
        ActionHandler *handler = nullptr;
        HRESULT hr = ref->resolve(&handler);
        if (SUCCEEDED(hr)) { hr = handler->Invoke(self, state); handler->Release(); }
        if (FAILED(hr)) trace("async completion callback failed", hr);
    }
public:
    REFERENCES
    HRESULT WINAPI QueryInterface(REFIID iid, void **out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid == IID_IUnknown || iid == IID_IInspectable || iid == IID_IAgileObject || iid == action_iid)
            *out = static_cast<Action *>(this);
        else if (iid == IID_IAsyncInfo) *out = static_cast<IAsyncInfo *>(this);
        else return E_NOINTERFACE;
        AddRef(); return S_OK;
    }
    INSPECTABLE(L"Windows.Foundation.IAsyncAction", action_iid, IID_IAsyncInfo)
    HRESULT WINAPI put_Completed(ActionHandler *handler) override {
        CallbackRef ref;
        HRESULT hr = capture(handler, action_handler_iid, ref);
        if (FAILED(hr)) return hr;
        AsyncStatus state;
        { Lock guard(lock);
          if (closed) return RO_E_CLOSED;
          if (assigned) return E_ILLEGAL_DELEGATE_ASSIGNMENT;
          assigned = true; completed = ref; state = status; }
        if (state != Started) invoke(ref, this, state);
        return S_OK;
    }
    HRESULT WINAPI get_Completed(ActionHandler **out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        CallbackRef ref;
        { Lock guard(lock); if (closed) return RO_E_CLOSED; ref = completed; }
        return ref ? ref->resolve(out) : S_OK;
    }
    HRESULT WINAPI GetResults() override {
        Lock guard(lock);
        if (closed) return RO_E_CLOSED;
        return status == Started ? E_ILLEGAL_METHOD_CALL : error;
    }
    HRESULT WINAPI get_Id(UINT32 *out) override {
        if (!out) return E_POINTER;
        Lock guard(lock); if (closed) return RO_E_CLOSED; *out = id; return S_OK;
    }
    HRESULT WINAPI get_Status(AsyncStatus *out) override {
        if (!out) return E_POINTER;
        Lock guard(lock); if (closed) return RO_E_CLOSED; *out = status; return S_OK;
    }
    HRESULT WINAPI get_ErrorCode(HRESULT *out) override {
        if (!out) return E_POINTER;
        Lock guard(lock); if (closed) return RO_E_CLOSED; *out = error; return S_OK;
    }
    HRESULT WINAPI Cancel() override {
        /* Shutdown is irrevocable. Cancellation is a request, not completion. */
        Lock guard(lock); return closed ? RO_E_CLOSED : S_OK;
    }
    HRESULT WINAPI Close() override {
        CallbackRef old;
        { Lock guard(lock);
          if (status == Started) return E_ILLEGAL_METHOD_CALL;
          closed = true; old.swap(completed); }
        return S_OK;
    }
    void finish(HRESULT hr) {
        CallbackRef ref; AsyncStatus state;
        { Lock guard(lock);
          if (status != Started) return;
          error = hr; state = status = FAILED(hr) ? Error : Completed; ref = completed; }
        invoke(ref, this, state);
    }
};

static INIT_ONCE init_once = INIT_ONCE_STATIC_INIT;
static DWORD queue_tls = TLS_OUT_OF_INDEXES;
static HINSTANCE module;
static const WCHAR window_class[] = L"GHUB.Windows.System.DispatcherQueue.lab";
static constexpr UINT wake_message = WM_APP + 0x471;
class Queue;
static LRESULT CALLBACK window_proc(HWND, UINT, WPARAM, LPARAM);
static BOOL CALLBACK initialize(PINIT_ONCE, PVOID, PVOID *)
{
    DWORD tls = TlsAlloc();
    if (tls == TLS_OUT_OF_INDEXES) return FALSE;
    WNDCLASSW wc = {};
    wc.lpfnWndProc = window_proc; wc.hInstance = module; wc.lpszClassName = window_class;
    if (!RegisterClassW(&wc)) { TlsFree(tls); return FALSE; }
    queue_tls = tls;
    return TRUE;
}
struct Event { Token token; CallbackRef callback; };
/* Weak resolution and the final strong release synchronize on the same lock.
 * A successful Resolve pins the target before making any interface call. */
class WeakReference final : public IWeakReference {
    LONG refs = 1;
    SRWLOCK lock = SRWLOCK_INIT;
    IUnknown *target;
public:
    explicit WeakReference(IUnknown *value) : target(value) {}
    REFERENCES
    HRESULT WINAPI QueryInterface(REFIID iid, void **out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid != IID_IUnknown && iid != IID_IAgileObject && iid != IID_IWeakReference) return E_NOINTERFACE;
        *out = static_cast<IWeakReference *>(this); AddRef(); return S_OK;
    }
    HRESULT WINAPI Resolve(REFIID iid, IInspectable **out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        IUnknown *value;
        { Lock guard(lock); value = target; if (value) value->AddRef(); }
        if (!value) return S_OK;
        HRESULT hr = value->QueryInterface(iid, reinterpret_cast<void **>(out));
        value->Release(); return hr;
    }
    ULONG release_target(LONG *strong) {
        Lock guard(lock);
        ULONG count = InterlockedDecrement(strong);
        if (!count) target = nullptr;
        return count;
    }
};
class Queue final : public DQueue, public DQueue2, public IWeakReferenceSource {
    LONG refs = 1;
    SRWLOCK lock = SRWLOCK_INIT;
    enum State { running, requested, starting, draining, stopped } state = running;
    HWND window = nullptr;
    DWORD thread = GetCurrentThreadId();
    bool dispatching = false, taking_deferrals = false;
    ULONG deferrals = 0;
    INT64 next_token = 0;
    // Empty lists allocate nothing, including in the window teardown path.
    std::list<CallbackRef> work[3];
    std::vector<Event> starting_events, completed_events;
    ShutdownAction *shutdown = nullptr;
    HRESULT shutdown_error = S_OK;
    WeakReference *weak = nullptr;
    HRESULT subscribe(EventHandler *handler, Token *token, bool start) {
        if (!token) return E_POINTER;
        token->value = 0;
        CallbackRef ref;
        HRESULT hr = capture(handler, start ? starting_iid : completed_iid, ref);
        if (FAILED(hr)) return hr;
        try {
            Lock guard(lock);
            if (state == stopped) return RO_E_CLOSED;
            Token value = {++next_token};
            (start ? starting_events : completed_events).push_back({value, ref});
            *token = value;
        } catch (const std::bad_alloc &) { return E_OUTOFMEMORY; }
        trace(start ? "add_ShutdownStarting" : "add_ShutdownCompleted");
        return S_OK;
    }
    HRESULT unsubscribe(Token token, bool start) {
        CallbackRef old;
        { Lock guard(lock);
          auto &events = start ? starting_events : completed_events;
          for (auto it = events.begin(); it != events.end(); ++it)
              if (it->token.value == token.value) { old.swap(it->callback); events.erase(it); break; } }
        return S_OK;
    }
    void fire(std::vector<Event> &events, IInspectable *args) {
        for (auto &event : events) {
            EventHandler *handler = nullptr;
            HRESULT hr = event.callback->resolve(&handler);
            if (SUCCEEDED(hr)) { hr = handler->Invoke(static_cast<DQueue *>(this), args); handler->Release(); }
            if (FAILED(hr)) trace("shutdown event failed", hr);
        }
    }
public:
    ~Queue() { if (shutdown) shutdown->Release(); if (weak) weak->Release(); }
    ULONG WINAPI AddRef() override { return InterlockedIncrement(&refs); }
    ULONG WINAPI Release() override {
        ULONG n = weak ? weak->release_target(&refs) : InterlockedDecrement(&refs);
        if (!n) delete this;
        return n;
    }
    HRESULT WINAPI QueryInterface(REFIID iid, void **out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid == IID_IUnknown || iid == IID_IInspectable || iid == IID_IAgileObject || iid == queue_iid)
            *out = static_cast<DQueue *>(this);
        else if (iid == queue2_iid) *out = static_cast<DQueue2 *>(this);
        else if (iid == IID_IWeakReferenceSource) *out = static_cast<IWeakReferenceSource *>(this);
        else return E_NOINTERFACE;
        AddRef(); return S_OK;
    }
    INSPECTABLE(L"Windows.System.DispatcherQueue", queue_iid, queue2_iid)
    HRESULT WINAPI GetWeakReference(IWeakReference **out) override {
        if (!out) return E_POINTER;
        weak->AddRef(); *out = weak; trace("GetWeakReference"); return S_OK;
    }
    HRESULT WINAPI CreateTimer(IInspectable **out) override {
        if (!out) return E_POINTER;
        *out = nullptr; trace("CreateTimer unsupported", E_NOTIMPL); return E_NOTIMPL;
    }
    HRESULT WINAPI TryEnqueue(Handler *handler, boolean *accepted) override {
        return TryEnqueueWithPriority(0, handler, accepted);
    }
    HRESULT WINAPI TryEnqueueWithPriority(INT priority, Handler *handler, boolean *accepted) override {
        if (!accepted) return E_POINTER;
        *accepted = false;
        if (!handler) return E_POINTER;
        if (priority != -10 && priority != 0 && priority != 10) return E_INVALIDARG;
        CallbackRef ref;
        HRESULT hr = capture(handler, handler_iid, ref);
        if (FAILED(hr)) return hr;
        try {
            Lock guard(lock);
            if (state >= draining) return S_OK;
            auto &list = work[(10 - priority) / 10];
            list.push_back(ref);
            if (!PostMessageW(window, wake_message, 0, 0)) {
                hr = HRESULT_FROM_WIN32(GetLastError()); list.pop_back(); return hr;
            }
            *accepted = true;
        } catch (const std::bad_alloc &) { return E_OUTOFMEMORY; }
        trace("TryEnqueue accepted"); return S_OK;
    }
    HRESULT WINAPI add_ShutdownStarting(EventHandler *h, Token *t) override { return subscribe(h, t, true); }
    HRESULT WINAPI remove_ShutdownStarting(Token t) override { return unsubscribe(t, true); }
    HRESULT WINAPI add_ShutdownCompleted(EventHandler *h, Token *t) override { return subscribe(h, t, false); }
    HRESULT WINAPI remove_ShutdownCompleted(Token t) override { return unsubscribe(t, false); }
    HRESULT WINAPI get_HasThreadAccess(boolean *out) override {
        if (!out) return E_POINTER;
        *out = thread == GetCurrentThreadId(); return S_OK;
    }
    HRESULT create_window() {
        weak = new (std::nothrow) WeakReference(static_cast<DQueue *>(this));
        if (!weak) return E_OUTOFMEMORY;
        HWND hwnd = CreateWindowExW(0, window_class, L"", 0, 0, 0, 0, 0, HWND_MESSAGE, nullptr, module, this);
        if (!hwnd) return HRESULT_FROM_WIN32(GetLastError());
        window = hwnd;
        return S_OK;
    }
    HRESULT begin_shutdown(Action **out) {
        if (!out) return E_POINTER;
        *out = nullptr;
        auto action = new (std::nothrow) ShutdownAction;
        if (!action) return E_OUTOFMEMORY;
        HRESULT hr = S_OK;
        { Lock guard(lock);
          if (state != running) hr = E_ILLEGAL_METHOD_CALL;
          else if (!PostMessageW(window, wake_message, 0, 0)) hr = HRESULT_FROM_WIN32(GetLastError());
          else { state = requested; shutdown = action; action->AddRef(); *out = action; } }
        /* On success the initial reference belongs to the queue, the extra
         * reference to the caller. On failure neither owns the new action. */
        if (FAILED(hr)) action->Release();
        else trace("ShutdownQueueAsync requested");
        return hr;
    }
    HRESULT take_deferral(DDeferral **);
    void end_deferral() {
        Lock guard(lock);
        if (state != starting || !deferrals) return;
        if (!--deferrals) { state = draining; PostMessageW(window, wake_message, 0, 0); }
    }
    void dispatch();
    void destroyed() {
        std::list<CallbackRef> abandoned[3];
        std::vector<Event> start, complete;
        { Lock guard(lock);
          window = nullptr; state = stopped;
          for (int i = 0; i < 3; ++i) abandoned[i].swap(work[i]);
          start.swap(starting_events); complete.swap(completed_events); }
        if (TlsGetValue(queue_tls) == this) TlsSetValue(queue_tls, nullptr);
        if (shutdown) shutdown->finish(RO_E_CLOSED);
    }
};
class Deferral final : public DDeferral {
    LONG refs = 1, done = 0;
    Queue *queue;
public:
    explicit Deferral(Queue *q) : queue(q) { q->AddRef(); }
    ~Deferral() { queue->Release(); }
    REFERENCES
    HRESULT WINAPI QueryInterface(REFIID iid, void **out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid != IID_IUnknown && iid != IID_IInspectable && iid != IID_IAgileObject && iid != deferral_iid)
            return E_NOINTERFACE;
        *out = static_cast<DDeferral *>(this); AddRef(); return S_OK;
    }
    INSPECTABLE(L"Windows.Foundation.Deferral", deferral_iid)
    HRESULT WINAPI Complete() override {
        if (InterlockedExchange(&done, 1)) return E_ILLEGAL_METHOD_CALL;
        queue->end_deferral(); return S_OK;
    }
};
HRESULT Queue::take_deferral(DDeferral **out)
{
    if (!out) return E_POINTER;
    *out = nullptr;
    auto value = new (std::nothrow) Deferral(this);
    if (!value) return E_OUTOFMEMORY;
    bool valid;
    { Lock guard(lock); valid = taking_deferrals && state == starting; if (valid) ++deferrals; }
    if (!valid) { value->Release(); return E_ILLEGAL_METHOD_CALL; }
    *out = value; return S_OK;
}
class StartingArgs final : public DArgs {
    LONG refs = 1;
    Queue *queue;
public:
    explicit StartingArgs(Queue *q) : queue(q) { q->AddRef(); }
    ~StartingArgs() { queue->Release(); }
    REFERENCES
    HRESULT WINAPI QueryInterface(REFIID iid, void **out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid != IID_IUnknown && iid != IID_IInspectable && iid != IID_IAgileObject && iid != args_iid)
            return E_NOINTERFACE;
        *out = static_cast<DArgs *>(this); AddRef(); return S_OK;
    }
    INSPECTABLE(L"Windows.System.DispatcherQueueShutdownStartingEventArgs", args_iid)
    HRESULT WINAPI GetDeferral(DDeferral **out) override { return queue->take_deferral(out); }
};
void Queue::dispatch()
{
    bool start = false;
    { Lock guard(lock);
      if (dispatching || state == stopped) return;
      dispatching = true;
      if (state == requested) { state = starting; taking_deferrals = true; deferrals = 1; start = true; } }
    AddRef(); // Events may release all their queue/controller references.
    if (start) {
        std::vector<Event> events;
        /* Events fire once. Move their owning references out without allocating. */
        { Lock guard(lock); events.swap(starting_events); }
        auto args = new (std::nothrow) StartingArgs(this);
        if (args) { fire(events, args); args->Release(); }
        else shutdown_error = E_OUTOFMEMORY;
        { Lock guard(lock); taking_deferrals = false; }
        end_deferral();
        trace("ShutdownStarting delivered");
    }
    CallbackRef callback;
    { Lock guard(lock);
      for (auto &list : work) if (!list.empty()) { callback.swap(list.front()); list.pop_front(); break; } }
    if (callback) {
        Handler *handler = nullptr;
        HRESULT hr = callback->resolve(&handler);
        if (SUCCEEDED(hr)) { hr = handler->Invoke(); handler->Release(); }
        trace("dispatch callback", hr);
    }
    bool finish = false;
    HWND hwnd;
    std::vector<Event> events;
    { Lock guard(lock);
      dispatching = false; hwnd = window;
      bool pending = !work[0].empty() || !work[1].empty() || !work[2].empty();
      if (state == draining && !pending) { state = stopped; finish = true; events.swap(completed_events); }
      else if (pending || state == requested) PostMessageW(window, wake_message, 0, 0); }
    if (finish) {
        fire(events, nullptr);
        shutdown->finish(shutdown_error);
        trace("ShutdownCompleted delivered");
        DestroyWindow(hwnd);
    }
    Release();
}
static LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    auto queue = reinterpret_cast<Queue *>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
    if (msg == WM_NCCREATE) {
        queue = static_cast<Queue *>(reinterpret_cast<CREATESTRUCTW *>(lp)->lpCreateParams);
        queue->AddRef(); SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(queue));
    }
    if (queue && msg == wake_message) { queue->dispatch(); return 0; }
    if (queue && msg == WM_NCDESTROY) {
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
        queue->destroyed(); queue->Release();
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}
class Controller final : public DController {
    LONG refs = 1;
    Queue *queue;
public:
    explicit Controller(Queue *q) : queue(q) { q->AddRef(); }
    ~Controller() { queue->Release(); }
    REFERENCES
    HRESULT WINAPI QueryInterface(REFIID iid, void **out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid != IID_IUnknown && iid != IID_IInspectable && iid != IID_IAgileObject && iid != controller_iid)
            return E_NOINTERFACE;
        *out = static_cast<DController *>(this); AddRef(); return S_OK;
    }
    INSPECTABLE(L"Windows.System.DispatcherQueueController", controller_iid)
    HRESULT WINAPI get_DispatcherQueue(DQueue **out) override {
        if (!out) return E_POINTER;
        queue->AddRef(); *out = queue; return S_OK;
    }
    HRESULT WINAPI ShutdownQueueAsync(Action **out) override { return queue->begin_shutdown(out); }
};
class Factory final : public IActivationFactory, public DStatics {
public:
    ULONG WINAPI AddRef() override { return 2; }
    ULONG WINAPI Release() override { return 1; }
    HRESULT WINAPI QueryInterface(REFIID iid, void **out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid == IID_IUnknown || iid == IID_IInspectable || iid == IID_IAgileObject || iid == IID_IActivationFactory)
            *out = static_cast<IActivationFactory *>(this);
        else if (iid == statics_iid) *out = static_cast<DStatics *>(this);
        else return E_NOINTERFACE;
        AddRef(); return S_OK;
    }
    INSPECTABLE(L"Windows.System.DispatcherQueue", statics_iid)
    HRESULT WINAPI ActivateInstance(IInspectable **out) override {
        if (!out) return E_POINTER;
        *out = nullptr; return E_NOTIMPL;
    }
    HRESULT WINAPI GetForCurrentThread(DQueue **out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (!InitOnceExecuteOnce(&init_once, initialize, nullptr, nullptr)) return HRESULT_FROM_WIN32(GetLastError());
        auto q = static_cast<Queue *>(TlsGetValue(queue_tls));
        if (q) { q->AddRef(); *out = q; }
        trace(q ? "GetForCurrentThread found" : "GetForCurrentThread empty");
        return S_OK;
    }
};
static Factory factory;
void ghub_install_tray_anchor();
extern "C" HRESULT WINAPI CreateDispatcherQueueController(Options options, DController **out)
{
    if (!out) return E_POINTER;
    *out = nullptr;
    if (options.size != sizeof(options) || (options.thread_type != 1 && options.thread_type != 2)
            || options.apartment_type < 0 || options.apartment_type > 2) return E_INVALIDARG;
    if (options.thread_type != 2 || options.apartment_type != 0) {
        trace("unsupported queue thread/apartment options", E_NOTIMPL); return E_NOTIMPL;
    }
    if (!InitOnceExecuteOnce(&init_once, initialize, nullptr, nullptr)) return HRESULT_FROM_WIN32(GetLastError());
    if (TlsGetValue(queue_tls)) return HRESULT_FROM_WIN32(ERROR_ALREADY_EXISTS);
    auto queue = new (std::nothrow) Queue;
    if (!queue) return E_OUTOFMEMORY;
    auto controller = new (std::nothrow) Controller(queue);
    if (!controller) { queue->Release(); return E_OUTOFMEMORY; }
    HRESULT hr = queue->create_window();
    if (SUCCEEDED(hr)) { TlsSetValue(queue_tls, queue); *out = controller; ghub_install_tray_anchor(); }
    else controller->Release();
    queue->Release();
    trace("CreateDispatcherQueueController", hr); return hr;
}
extern "C" HRESULT WINAPI DllGetActivationFactory(HSTRING name, IActivationFactory **out)
{
    if (!out) return E_POINTER;
    *out = nullptr;
    UINT32 len;
    const WCHAR *text = WindowsGetStringRawBuffer(name, &len);
    static const WCHAR expected[] = L"Windows.System.DispatcherQueue";
    if (len == ARRAYSIZE(expected) - 1 && !wmemcmp(text, expected, len)) {
        *out = &factory; factory.AddRef(); trace("DispatcherQueue activation factory"); return S_OK;
    }
    /* Preserve the pinned Wine implementation for all unrelated classes. */
    HMODULE fallback = GetModuleHandleW(L"ghub_wine_coremessaging.dll");
    if (!fallback) fallback = LoadLibraryW(L"ghub_wine_coremessaging.dll");
    using GetFactory = HRESULT (WINAPI *)(HSTRING, IActivationFactory **);
    auto get = reinterpret_cast<GetFactory>(fallback ? GetProcAddress(fallback, "DllGetActivationFactory") : nullptr);
    return get ? get(name, out) : CLASS_E_CLASSNOTAVAILABLE;
}
extern "C" HRESULT WINAPI DllCanUnloadNow() { return S_FALSE; }
extern "C" BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH) module = instance;
    return TRUE;
}
