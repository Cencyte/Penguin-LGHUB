/* Independent SDK C ABI checks. Runs only in a private Xvfb Wine laboratory. */
#define COBJMACROS
#define CONST_VTABLE
#define INITGUID
#include <windows.h>
#include <roapi.h>
#include <winstring.h>
#include <windows.system.h>
#include <weakreference.h>
#include <stdio.h>

/* dispatcherqueue.h in this SDK is C++-only. The COM interfaces below are
 * unmodified SDK declarations; only the three-field Win32 options need this. */
typedef struct { DWORD dwSize; INT threadType; INT apartmentType; } DispatcherQueueOptions;
enum { DQTYPE_THREAD_CURRENT = 2, DQTAT_COM_NONE = 0 };

typedef __x_ABI_CWindows_CSystem_CIDispatcherQueue Queue;
typedef __x_ABI_CWindows_CSystem_CIDispatcherQueue2 Queue2;
typedef __x_ABI_CWindows_CSystem_CIDispatcherQueueStatics Statics;
typedef __x_ABI_CWindows_CSystem_CIDispatcherQueueController Controller;
typedef __x_ABI_CWindows_CSystem_CIDispatcherQueueHandler Handler;
typedef __x_ABI_CWindows_CSystem_CIDispatcherQueueShutdownStartingEventArgs Args;
typedef __x_ABI_CWindows_CFoundation_CIDeferral Deferral;
typedef __x_ABI_CWindows_CFoundation_CIAsyncAction Action;
typedef __x_ABI_CWindows_CFoundation_CIAsyncActionCompletedHandler ActionHandler;
typedef __FITypedEventHandler_2_Windows__CSystem__CDispatcherQueue_IInspectable CompletedHandler;
typedef __FITypedEventHandler_2_Windows__CSystem__CDispatcherQueue_Windows__CSystem__CDispatcherQueueShutdownStartingEventArgs StartingHandler;
#define SYS_IID(name) IID___x_ABI_CWindows_CSystem_CI##name
#define FOUNDATION_IID(name) IID___x_ABI_CWindows_CFoundation_CI##name
#define CALL(obj, method, ...) ((obj)->lpVtbl->method((obj), ##__VA_ARGS__))
static unsigned checks, failures;
#define ok(value, message) do { ++checks; if (!(value)) { ++failures; printf("FAIL line %d: %s\n", __LINE__, message); } } while (0)
#define hr_ok(value) ok((value) == S_OK, #value)
static DWORD owner;
static int order[64], count, in_callback, starting_count, completed_count, action_count;
static Queue *queue;
static Statics *statics;
static Deferral *deferral;
static Args *saved_args;
static Action *shutdown_action;

struct callback { Handler iface; LONG refs; int value; BOOL pump; };
static ULONG WINAPI cb_addref(Handler *iface) { return InterlockedIncrement(&((struct callback *)iface)->refs); }
static ULONG WINAPI cb_release(Handler *iface) { return InterlockedDecrement(&((struct callback *)iface)->refs); }
static HRESULT WINAPI cb_query(Handler *iface, REFIID iid, void **out)
{
    *out = NULL;
    if (!IsEqualGUID(iid, &IID_IUnknown) && !IsEqualGUID(iid, &IID_IAgileObject)
            && !IsEqualGUID(iid, &SYS_IID(DispatcherQueueHandler))) return E_NOINTERFACE;
    *out = iface; cb_addref(iface); return S_OK;
}
static void pump(void)
{
    MSG msg;
    while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
}
static HRESULT WINAPI cb_invoke(Handler *iface)
{
    struct callback *cb = (struct callback *)iface;
    ok(GetCurrentThreadId() == owner, "callback thread affinity");
    ok(!in_callback, "callbacks are serial even across a nested message pump");
    ++in_callback;
    order[count++] = cb->value;
    if (cb->pump) pump();
    --in_callback;
    return cb->value == 7 ? E_FAIL : S_OK; /* A failure must not wedge the next callback. */
}
static const __x_ABI_CWindows_CSystem_CIDispatcherQueueHandlerVtbl cb_vtbl = {
    cb_query, cb_addref, cb_release, cb_invoke
};
static struct callback callbacks[10];
static void enqueue(unsigned index, int priority)
{
    boolean accepted = FALSE;
    hr_ok(CALL(queue, TryEnqueueWithPriority, priority, &callbacks[index].iface, &accepted));
    ok(accepted, "enqueue accepted");
}
static LONG starting_refs = 1, completed_refs = 1, action_refs = 1;
static ULONG WINAPI starting_add(StartingHandler *self) { (void)self; return InterlockedIncrement(&starting_refs); }
static ULONG WINAPI starting_release(StartingHandler *self) { (void)self; return InterlockedDecrement(&starting_refs); }
static HRESULT WINAPI starting_query(StartingHandler *self, REFIID iid, void **out)
{
    *out = NULL;
    if (!IsEqualGUID(iid, &IID_IUnknown) && !IsEqualGUID(iid, &IID_IAgileObject) &&
        !IsEqualGUID(iid, &IID___FITypedEventHandler_2_Windows__CSystem__CDispatcherQueue_Windows__CSystem__CDispatcherQueueShutdownStartingEventArgs))
        return E_NOINTERFACE;
    *out = self; starting_add(self); return S_OK;
}
static HRESULT WINAPI on_starting(StartingHandler *self, Queue *sender, Args *args)
{
    (void)self;
    ++starting_count;
    ok(sender == queue && GetCurrentThreadId() == owner, "starting sender and thread");
    ok(args != NULL, "starting arguments");
    hr_ok(CALL(args, GetDeferral, &deferral));
    saved_args = args; CALL(args, AddRef);
    enqueue(5, 0);
    return S_OK;
}
static const __FITypedEventHandler_2_Windows__CSystem__CDispatcherQueue_Windows__CSystem__CDispatcherQueueShutdownStartingEventArgsVtbl starting_vtbl = {
    starting_query, starting_add, starting_release, on_starting
};
static StartingHandler starting_handler = {&starting_vtbl};
static ULONG WINAPI completed_add(CompletedHandler *self) { (void)self; return InterlockedIncrement(&completed_refs); }
static ULONG WINAPI completed_release(CompletedHandler *self) { (void)self; return InterlockedDecrement(&completed_refs); }
static HRESULT WINAPI completed_query(CompletedHandler *self, REFIID iid, void **out)
{
    *out = NULL;
    if (!IsEqualGUID(iid, &IID_IUnknown) && !IsEqualGUID(iid, &IID_IAgileObject) &&
        !IsEqualGUID(iid, &IID___FITypedEventHandler_2_Windows__CSystem__CDispatcherQueue_IInspectable)) return E_NOINTERFACE;
    *out = self; completed_add(self); return S_OK;
}
static HRESULT WINAPI on_completed(CompletedHandler *self, Queue *sender, IInspectable *args)
{
    boolean accepted = TRUE;
    (void)self;
    ++completed_count;
    ok(sender == queue && GetCurrentThreadId() == owner, "completed sender and thread");
    ok(args == NULL, "completed event arguments");
    ok(action_count == 0, "queue event precedes async completion");
    hr_ok(CALL(queue, TryEnqueue, &callbacks[0].iface, &accepted));
    ok(!accepted, "completed queue rejects work");
    return S_OK;
}
static const __FITypedEventHandler_2_Windows__CSystem__CDispatcherQueue_IInspectableVtbl completed_vtbl = {
    completed_query, completed_add, completed_release, on_completed
};
static CompletedHandler completed_handler = {&completed_vtbl};
static ULONG WINAPI action_add(ActionHandler *self) { (void)self; return InterlockedIncrement(&action_refs); }
static ULONG WINAPI action_release(ActionHandler *self) { (void)self; return InterlockedDecrement(&action_refs); }
static HRESULT WINAPI action_query(ActionHandler *self, REFIID iid, void **out)
{
    *out = NULL;
    if (!IsEqualGUID(iid, &IID_IUnknown) && !IsEqualGUID(iid, &IID_IAgileObject)
            && !IsEqualGUID(iid, &FOUNDATION_IID(AsyncActionCompletedHandler))) return E_NOINTERFACE;
    *out = self; action_add(self); return S_OK;
}
static HRESULT WINAPI on_action(ActionHandler *self, Action *sender, AsyncStatus status)
{
    (void)self;
    ++action_count;
    ok(sender == shutdown_action && status == Completed, "async completion identity and status");
    ok(completed_count == 1, "async completes after shutdown event");
    hr_ok(CALL(sender, GetResults));
    return S_OK;
}
static const __x_ABI_CWindows_CFoundation_CIAsyncActionCompletedHandlerVtbl action_vtbl = {
    action_query, action_add, action_release, on_action
};
static ActionHandler action_handler = {&action_vtbl};
static DWORD WINAPI worker(void *unused)
{
    Queue *other = (Queue *)(ULONG_PTR)1;
    Queue2 *q2 = NULL;
    boolean access = TRUE;
    (void)unused;
    hr_ok(RoInitialize(RO_INIT_MULTITHREADED));
    hr_ok(CALL(statics, GetForCurrentThread, &other));
    ok(other == NULL, "another thread has no associated queue");
    hr_ok(CALL(queue, QueryInterface, &SYS_IID(DispatcherQueue2), (void **)&q2));
    hr_ok(CALL(q2, get_HasThreadAccess, &access));
    ok(!access, "cross-thread HasThreadAccess is false");
    CALL(q2, Release);
    enqueue(4, 0);
    RoUninitialize();
    return 0;
}
struct weak_race { IWeakReference *weak; HANDLE ready; HRESULT result; BOOL saw_live, saw_expired; };
static DWORD WINAPI resolve_worker(void *context)
{
    struct weak_race *race = context;
    ULONGLONG end = GetTickCount64() + 5000;
    HRESULT init = RoInitialize(RO_INIT_MULTITHREADED);
    race->result = init;
    if (FAILED(init)) { SetEvent(race->ready); return 1; }
    do {
        IInspectable *value = NULL;
        race->result = CALL(race->weak, Resolve, &SYS_IID(DispatcherQueue), &value);
        if (FAILED(race->result)) break;
        if (value) { race->saw_live = TRUE; CALL(value, Release); }
        else race->saw_expired = TRUE;
        SetEvent(race->ready);
        if (race->saw_expired) break;
        SwitchToThread();
    } while (GetTickCount64() < end);
    SetEvent(race->ready);
    RoUninitialize();
    return 0;
}
int wmain(int argc, WCHAR **argv)
{
    HMODULE module;
    HRESULT (WINAPI *get_factory)(HSTRING, IActivationFactory **);
    HRESULT (WINAPI *create)(DispatcherQueueOptions, Controller **);
    IActivationFactory *factory;
    HSTRING name;
    Queue *same;
    Queue2 *q2;
    Controller *controller, *duplicate;
    IAsyncInfo *info;
    IUnknown *identity, *identity2;
    IWeakReferenceSource *weak_source;
    IWeakReference *weak;
    IInspectable *resolved;
    __x_ABI_CWindows_CSystem_CIDispatcherQueueTimer *timer;
    struct weak_race race = {0};
    AsyncStatus state;
    EventRegistrationToken start_token, complete_token, removed_token;
    boolean accepted, access;
    DispatcherQueueOptions options = {sizeof(options), DQTYPE_THREAD_CURRENT, DQTAT_COM_NONE};
    HANDLE thread;
    HRESULT hr;
    unsigned i;
    if (argc != 2) return 2;
    setbuf(stdout, NULL);
    owner = GetCurrentThreadId();
    hr_ok(RoInitialize(RO_INIT_SINGLETHREADED));
    module = LoadLibraryExW(argv[1], NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    ok(module != NULL, "load adapter");
    if (!module) { printf("LoadLibrary error=%lu\n", GetLastError()); return 1; }
    get_factory = (void *)GetProcAddress(module, "DllGetActivationFactory");
    create = (void *)GetProcAddress(module, "CreateDispatcherQueueController");
    ok(get_factory && create, "export resolution");
    if (!get_factory || !create) return 1;
    hr_ok(WindowsCreateString(L"Windows.System.DispatcherQueue", 30, &name));
    hr_ok(get_factory(name, &factory));
    hr_ok(RoGetActivationFactory(name, &SYS_IID(DispatcherQueueStatics), (void **)&statics));
    if (!statics) return 1;
    CALL(statics, Release);
    WindowsDeleteString(name);
    hr_ok(CALL(factory, QueryInterface, &SYS_IID(DispatcherQueueStatics), (void **)&statics));
    CALL(factory, Release);
    same = (Queue *)(ULONG_PTR)1;
    hr_ok(CALL(statics, GetForCurrentThread, &same));
    ok(!same, "no queue before creation");
    options.dwSize = 0;
    ok(create(options, &controller) == E_INVALIDARG && !controller, "invalid options rejected");
    options.dwSize = sizeof(options);
    options.threadType = 1;
    ok(create(options, &controller) == E_NOTIMPL && !controller, "dedicated threads explicitly unsupported");
    options.threadType = DQTYPE_THREAD_CURRENT;
    hr_ok(create(options, &controller));
    hr_ok(CALL(controller, get_DispatcherQueue, &queue));
    hr_ok(CALL(statics, GetForCurrentThread, &same));
    ok(same == queue, "current-thread identity"); CALL(same, Release);
    hr = create(options, &duplicate);
    ok(FAILED(hr) && !duplicate, "duplicate queue fails");
    hr_ok(CALL(queue, QueryInterface, &SYS_IID(DispatcherQueue2), (void **)&q2));
    hr_ok(CALL(q2, get_HasThreadAccess, &access)); ok(access, "owner thread access");
    hr_ok(CALL(queue, QueryInterface, &IID_IUnknown, (void **)&identity));
    hr_ok(CALL(q2, QueryInterface, &IID_IUnknown, (void **)&identity2));
    ok(identity == identity2, "queue interfaces share COM identity");
    CALL(identity, Release); CALL(identity2, Release); CALL(q2, Release);
    hr_ok(CALL(queue, QueryInterface, &IID_IWeakReferenceSource, (void **)&weak_source));
    hr_ok(CALL(weak_source, GetWeakReference, &weak)); CALL(weak_source, Release);
    hr_ok(CALL(weak, Resolve, &SYS_IID(DispatcherQueue), &resolved));
    ok(resolved == (IInspectable *)queue, "weak reference resolves live queue"); CALL(resolved, Release);
    timer = (void *)(ULONG_PTR)1;
    ok(CALL(queue, CreateTimer, &timer) == E_NOTIMPL && !timer, "timers explicitly unsupported");
    for (i = 0; i < ARRAYSIZE(callbacks); ++i) {
        callbacks[i].iface.lpVtbl = &cb_vtbl; callbacks[i].refs = 1; callbacks[i].value = i;
    }
    callbacks[1].pump = TRUE;
    enqueue(0, -10); enqueue(1, 10); enqueue(2, 0); enqueue(3, 10);
    thread = CreateThread(NULL, 0, worker, NULL, 0, NULL);
    ok(WaitForSingleObject(thread, 10000) == WAIT_OBJECT_0, "cross-thread enqueue completed"); CloseHandle(thread);
    ok(count == 0, "enqueue does not run inline");
    pump();
    ok(count == 5 && order[0] == 1 && order[1] == 3 && order[2] == 2 && order[3] == 4 && order[4] == 0,
       "priority ordering with FIFO ties");
    accepted = TRUE;
    ok(CALL(queue, TryEnqueueWithPriority, 42, &callbacks[0].iface, &accepted) == E_INVALIDARG && !accepted,
       "invalid priority rejected");
    ok(CALL(queue, TryEnqueue, NULL, &accepted) == E_POINTER, "null callback rejected");
    hr_ok(CALL(queue, add_ShutdownStarting, &starting_handler, &start_token));
    hr_ok(CALL(queue, add_ShutdownCompleted, &completed_handler, &complete_token));
    hr_ok(CALL(queue, add_ShutdownCompleted, &completed_handler, &removed_token));
    ok(removed_token.value != complete_token.value, "distinct event tokens");
    hr_ok(CALL(queue, remove_ShutdownCompleted, removed_token));
    enqueue(7, 0); enqueue(8, 0);
    hr_ok(CALL(controller, ShutdownQueueAsync, &shutdown_action));
    hr_ok(CALL(shutdown_action, QueryInterface, &IID_IAsyncInfo, (void **)&info));
    hr_ok(CALL(info, get_Status, &state)); ok(state == Started, "shutdown starts asynchronously");
    ok(FAILED(CALL(shutdown_action, GetResults)), "no premature successful shutdown");
    ok(FAILED(CALL(info, Close)), "cannot close pending shutdown");
    hr_ok(CALL(shutdown_action, put_Completed, &action_handler));
    ok(FAILED(CALL(shutdown_action, put_Completed, &action_handler)), "completion delegate assigned once");
    pump();
    ok(starting_count == 1 && completed_count == 0 && action_count == 0, "deferral holds shutdown open");
    ok(count == 8 && order[5] == 7 && order[6] == 8 && order[7] == 5, "failure and shutdown cleanup drained");
    hr_ok(CALL(info, get_Status, &state)); ok(state == Started, "still pending during deferral");
    enqueue(6, 0); pump();
    ok(count == 9 && order[8] == 6, "work accepted during deferral");
    hr_ok(CALL(deferral, Complete));
    ok(FAILED(CALL(deferral, Complete)), "deferral completes once");
    CALL(deferral, Release); CALL(saved_args, Release);
    accepted = TRUE;
    hr_ok(CALL(queue, TryEnqueue, &callbacks[0].iface, &accepted)); ok(!accepted, "draining rejects work");
    pump();
    ok(completed_count == 1 && action_count == 1, "one shutdown event and one completion");
    hr_ok(CALL(info, get_Status, &state)); ok(state == Completed, "completed status");
    hr_ok(CALL(shutdown_action, GetResults));
    hr_ok(CALL(queue, remove_ShutdownStarting, start_token));
    hr_ok(CALL(queue, remove_ShutdownCompleted, complete_token));
    hr_ok(CALL(info, Close));
    ok(FAILED(CALL(shutdown_action, GetResults)), "closed action rejects access");
    CALL(info, Release); CALL(shutdown_action, Release);
    race.weak = weak;
    race.ready = CreateEventW(NULL, TRUE, FALSE, NULL);
    thread = CreateThread(NULL, 0, resolve_worker, &race, 0, NULL);
    ok(WaitForSingleObject(race.ready, 10000) == WAIT_OBJECT_0, "weak resolver started");
    CALL(controller, Release); CALL(queue, Release); CALL(statics, Release);
    ok(WaitForSingleObject(thread, 10000) == WAIT_OBJECT_0, "weak resolution survives final release race");
    ok(race.result == S_OK && race.saw_live && race.saw_expired, "concurrent weak reference transitions live to expired");
    CloseHandle(thread); CloseHandle(race.ready);
    resolved = (IInspectable *)(ULONG_PTR)1;
    hr_ok(CALL(weak, Resolve, &SYS_IID(DispatcherQueue), &resolved));
    ok(!resolved, "weak reference expires after final release"); CALL(weak, Release);
    for (i = 0; i < ARRAYSIZE(callbacks); ++i) ok(callbacks[i].refs == 1, "callback reference released");
    ok(starting_refs == 1 && completed_refs == 1 && action_refs == 1, "event references released");
    RoUninitialize();
    printf("DispatcherQueue: %u checks, %u failures\n", checks, failures);
    return failures ? 1 : 0;
}
