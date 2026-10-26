/* Focused Windows.System.Threading.ThreadPoolTimer ABI and lifetime probe. */
#define COBJMACROS
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

#include "initguid.h"
#include "roapi.h"
#include "winstring.h"
#include "activation.h"

DEFINE_GUID(IID_IThreadPoolTimer, 0x594ebe78, 0x55ea, 0x4a88, 0xa5,0x0d,0x34,0x02,0xae,0x1f,0x9f,0xc2);
DEFINE_GUID(IID_IThreadPoolTimerStatics, 0x1a8a9d02, 0xe482, 0x461b, 0xb8,0xc7,0x8e,0xfa,0xd1,0xcc,0xe5,0x90);
DEFINE_GUID(IID_TimerElapsedHandler, 0xfaaea667, 0xfbeb, 0x49cb, 0xad,0xb2,0x71,0x18,0x4c,0x55,0x6e,0x43);
DEFINE_GUID(IID_TimerDestroyedHandler, 0x34ed19fa, 0x8384, 0x4eb9, 0x82,0x09,0xfb,0x50,0x94,0xee,0xec,0x35);

typedef struct timer_iface timer_iface;
typedef struct timer_statics timer_statics;
typedef struct timer_vtbl timer_vtbl;
typedef struct timer_statics_vtbl timer_statics_vtbl;
typedef struct timer_handler_vtbl timer_handler_vtbl;

typedef struct { LONGLONG Duration; } TimeSpan;

struct timer_iface { const timer_vtbl *lpVtbl; };
struct timer_vtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(timer_iface *, REFIID, void **);
    ULONG (STDMETHODCALLTYPE *AddRef)(timer_iface *);
    ULONG (STDMETHODCALLTYPE *Release)(timer_iface *);
    HRESULT (STDMETHODCALLTYPE *GetIids)(timer_iface *, ULONG *, IID **);
    HRESULT (STDMETHODCALLTYPE *GetRuntimeClassName)(timer_iface *, HSTRING *);
    HRESULT (STDMETHODCALLTYPE *GetTrustLevel)(timer_iface *, TrustLevel *);
    HRESULT (STDMETHODCALLTYPE *get_Period)(timer_iface *, TimeSpan *);
    HRESULT (STDMETHODCALLTYPE *get_Delay)(timer_iface *, TimeSpan *);
    HRESULT (STDMETHODCALLTYPE *Cancel)(timer_iface *);
};

struct timer_statics { const timer_statics_vtbl *lpVtbl; };
struct timer_statics_vtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(timer_statics *, REFIID, void **);
    ULONG (STDMETHODCALLTYPE *AddRef)(timer_statics *);
    ULONG (STDMETHODCALLTYPE *Release)(timer_statics *);
    HRESULT (STDMETHODCALLTYPE *GetIids)(timer_statics *, ULONG *, IID **);
    HRESULT (STDMETHODCALLTYPE *GetRuntimeClassName)(timer_statics *, HSTRING *);
    HRESULT (STDMETHODCALLTYPE *GetTrustLevel)(timer_statics *, TrustLevel *);
    HRESULT (STDMETHODCALLTYPE *CreatePeriodicTimer)(timer_statics *, IUnknown *, TimeSpan, timer_iface **);
    HRESULT (STDMETHODCALLTYPE *CreateTimer)(timer_statics *, IUnknown *, TimeSpan, timer_iface **);
    HRESULT (STDMETHODCALLTYPE *CreatePeriodicTimerWithCompletion)(timer_statics *, IUnknown *, TimeSpan, IUnknown *, timer_iface **);
    HRESULT (STDMETHODCALLTYPE *CreateTimerWithCompletion)(timer_statics *, IUnknown *, TimeSpan, IUnknown *, timer_iface **);
};

struct timer_handler_vtbl {
    HRESULT (STDMETHODCALLTYPE *QueryInterface)(IUnknown *, REFIID, void **);
    ULONG (STDMETHODCALLTYPE *AddRef)(IUnknown *);
    ULONG (STDMETHODCALLTYPE *Release)(IUnknown *);
    HRESULT (STDMETHODCALLTYPE *Invoke)(IUnknown *, timer_iface *);
};

struct callback_handler {
    IUnknown iface;
    LONG refs;
    LONG calls;
    HANDLE event;
    HANDLE gate;
    BOOL cancel_inside, release_inside, inspect;
    LONG callback_failures;
    LONG finished;
    timer_iface *retained;
};

static struct callback_handler *impl_from_handler(IUnknown *iface)
{
    return CONTAINING_RECORD(iface, struct callback_handler, iface);
}

static HRESULT STDMETHODCALLTYPE handler_QueryInterface(IUnknown *iface, REFIID iid, void **out)
{
    if (!out) return E_POINTER;
    *out = NULL;
    if (IsEqualIID(iid, &IID_IUnknown) || IsEqualIID(iid, &IID_TimerElapsedHandler)
            || IsEqualIID(iid, &IID_TimerDestroyedHandler))
    {
        *out = iface;
        iface->lpVtbl->AddRef(iface);
        return S_OK;
    }
    return E_NOINTERFACE;
}

static ULONG STDMETHODCALLTYPE handler_AddRef(IUnknown *iface)
{
    return InterlockedIncrement(&impl_from_handler(iface)->refs);
}

static ULONG STDMETHODCALLTYPE handler_Release(IUnknown *iface)
{
    struct callback_handler *handler = impl_from_handler(iface);
    ULONG refs = InterlockedDecrement(&handler->refs);
    if (!refs)
    {
        CloseHandle(handler->event);
        if (handler->gate) CloseHandle(handler->gate);
        free(handler);
    }
    return refs;
}

static HRESULT STDMETHODCALLTYPE handler_Invoke(IUnknown *iface, timer_iface *timer)
{
    struct callback_handler *handler = impl_from_handler(iface);
    IUnknown *identity = NULL;
    TimeSpan duration;
    InterlockedIncrement(&handler->calls);
    if (handler->cancel_inside && timer->lpVtbl->Cancel(timer) != S_OK)
        InterlockedIncrement(&handler->callback_failures);
    SetEvent(handler->event);
    if (handler->gate && WaitForSingleObject(handler->gate, 5000) != WAIT_OBJECT_0)
        InterlockedIncrement(&handler->callback_failures);
    if (handler->inspect)
    {
        if (timer->lpVtbl->get_Delay(timer, &duration) != S_OK
                || timer->lpVtbl->QueryInterface(timer, &IID_IUnknown, (void **)&identity) != S_OK)
            InterlockedIncrement(&handler->callback_failures);
        if (identity) IUnknown_Release(identity);
        timer->lpVtbl->AddRef(timer);
        handler->retained = timer;
    }
    if (handler->release_inside) timer->lpVtbl->Release(timer);
    InterlockedExchange(&handler->finished, 1);
    return S_OK;
}

static const timer_handler_vtbl handler_vtbl = {
    handler_QueryInterface, handler_AddRef, handler_Release, handler_Invoke,
};

static struct callback_handler *create_handler(void)
{
    struct callback_handler *handler = calloc(1, sizeof(*handler));
    if (!handler) return NULL;
    handler->iface.lpVtbl = (IUnknownVtbl *)&handler_vtbl;
    handler->refs = 1;
    handler->event = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (!handler->event)
    {
        free(handler);
        return NULL;
    }
    return handler;
}

static unsigned checks;

static int check(BOOL condition, const char *message, HRESULT hr)
{
    ++checks;
    if (!condition)
    {
        fprintf(stderr, "FAIL: %s (hr=%#lx, last-error=%lu)\n", message, hr, GetLastError());
        return 1;
    }
    return 0;
}


static BOOL wait_value(LONG *value, LONG expected)
{
    ULONGLONG end = GetTickCount64() + 5000;
    while (InterlockedCompareExchange(value, 0, 0) != expected && GetTickCount64() < end)
        Sleep(1);
    return InterlockedCompareExchange(value, 0, 0) == expected;
}

static int lifetime_tests(timer_statics *statics)
{
    int failures = 0;
    unsigned mode, i;
    HRESULT hr;
    timer_iface *timer, *invalid;
    struct callback_handler *handler, *destroyed;
    TimeSpan short_delay = {100000}, long_delay = {600000000}, sub_ms = {9999};

    handler = create_handler();
    if (!handler) return check(FALSE, "allocate validation handler", E_OUTOFMEMORY);
    invalid = (void *)1;
    hr = statics->lpVtbl->CreateTimer(statics, NULL, short_delay, &invalid);
    failures += check(hr == E_INVALIDARG && !invalid, "null handler clears output", hr);
    hr = statics->lpVtbl->CreateTimer(statics, &handler->iface, short_delay, NULL);
    failures += check(hr == E_POINTER, "null output", hr);
    hr = statics->lpVtbl->CreateTimer(statics, &handler->iface, (TimeSpan){-1}, &invalid);
    failures += check(hr == E_INVALIDARG && !invalid, "negative duration", hr);
    IUnknown_Release(&handler->iface);

    /* Natural one-shot, cancel before due, zero/sub-ms periodic, then self-cancel
     * and release. A held callback exposes completion ordering deterministically. */
    for (mode = 0; mode < 6; ++mode)
    {
        handler = create_handler();
        destroyed = create_handler();
        if (!handler || !destroyed) ExitProcess(2);
        destroyed->inspect = TRUE;
        destroyed->cancel_inside = TRUE; /* reentrant completion */
        if (mode == 4 || mode == 5)
        {
            handler->gate = CreateEventW(NULL, TRUE, FALSE, NULL);
            if (mode == 4) handler->cancel_inside = handler->release_inside = TRUE;
        }
        timer = NULL;
        if (mode == 2 || mode == 3 || mode == 4)
            hr = statics->lpVtbl->CreatePeriodicTimerWithCompletion(statics, &handler->iface,
                    mode == 2 ? (TimeSpan){0} : mode == 3 ? sub_ms : short_delay,
                    &destroyed->iface, &timer);
        else
            hr = statics->lpVtbl->CreateTimerWithCompletion(statics, &handler->iface,
                    mode == 1 ? long_delay : short_delay, &destroyed->iface, &timer);
        failures += check(hr == S_OK && timer, "lifetime timer creation", hr);
        if (FAILED(hr)) ExitProcess(2);
        if (mode == 1)
        {
            failures += check(timer->lpVtbl->Cancel(timer) == S_OK, "cancel before expiration", S_OK);
            failures += check(timer->lpVtbl->Cancel(timer) == S_OK, "cancel idempotent", S_OK);
        }
        if (mode == 4 || mode == 5)
        {
            failures += check(WaitForSingleObject(handler->event, 5000) == WAIT_OBJECT_0,
                    "callback entered", S_OK);
            if (mode == 5)
                failures += check(timer->lpVtbl->Cancel(timer) == S_OK, "cancel during held callback returns", S_OK);
            failures += check(WaitForSingleObject(destroyed->event, 50) == WAIT_TIMEOUT,
                    "completion waits for running callback", S_OK);
            SetEvent(handler->gate);
            if (mode == 4) timer = NULL; /* callback owns caller's reference now */
        }
        failures += check(wait_value(&destroyed->finished, 1), "completion while caller still owns object", S_OK);
        failures += check(handler->calls == (mode == 1 ? 0 : 1), "lifetime elapsed count", S_OK);
        failures += check(destroyed->calls == 1, "completion exactly once", S_OK);
        failures += check(!handler->callback_failures && !destroyed->callback_failures,
                "callback reentrancy and property access", S_OK);
        failures += check(wait_value(&handler->refs, 1) && wait_value(&destroyed->refs, 1),
                "delegates released after completion", S_OK);
        if (timer) timer->lpVtbl->Release(timer);
        if (destroyed->retained)
        {
            failures += check(destroyed->retained->lpVtbl->Cancel(destroyed->retained) == S_OK,
                    "timer retained by completion remains valid", S_OK);
            destroyed->retained->lpVtbl->Release(destroyed->retained);
        }
        IUnknown_Release(&handler->iface);
        IUnknown_Release(&destroyed->iface);
    }

    /* Rapid cancel/release versus immediate dispatch. Completion must outlive
     * the caller reference and neither delegate may be leaked or called twice. */
    for (i = 0; i < 128; ++i)
    {
        handler = create_handler();
        destroyed = create_handler();
        if (!handler || !destroyed) ExitProcess(2);
        hr = statics->lpVtbl->CreateTimerWithCompletion(statics, &handler->iface,
                (TimeSpan){i % 2 ? 0 : 10000}, &destroyed->iface, &timer);
        failures += check(hr == S_OK, "cancel/release stress create", hr);
        if (FAILED(hr)) ExitProcess(2);
        timer->lpVtbl->Cancel(timer);
        timer->lpVtbl->Release(timer);
        failures += check(wait_value(&destroyed->finished, 1), "cancel/release stress completion", S_OK);
        failures += check(destroyed->calls == 1 && handler->calls <= 1,
                "cancel/release stress invocation counts", S_OK);
        failures += check(wait_value(&handler->refs, 1) && wait_value(&destroyed->refs, 1),
                "cancel/release stress delegate release", S_OK);
        IUnknown_Release(&handler->iface);
        IUnknown_Release(&destroyed->iface);
    }
    return failures;
}

int wmain(void)
{
    IActivationFactory *factory = NULL;
    timer_statics *statics = NULL;
    struct callback_handler *handler, *destroyed;
    timer_iface *timer = NULL;
    HSTRING classid = NULL;
    TimeSpan duration = {200000}; /* 20 ms */
    TimeSpan period = {0}, delay = {0};
    HRESULT hr;
    LONG calls;
    int failures = 0;

    hr = RoInitialize(RO_INIT_MULTITHREADED);
    failures += check(SUCCEEDED(hr), "RoInitialize", hr);
    hr = WindowsCreateString(L"Windows.System.Threading.ThreadPoolTimer", 40, &classid);
    failures += check(SUCCEEDED(hr), "WindowsCreateString", hr);
    hr = RoGetActivationFactory(classid, &IID_IActivationFactory, (void **)&factory);
    failures += check(SUCCEEDED(hr), "RoGetActivationFactory(ThreadPoolTimer)", hr);
    if (FAILED(hr)) goto done;
    hr = IActivationFactory_QueryInterface(factory, &IID_IThreadPoolTimerStatics, (void **)&statics);
    failures += check(SUCCEEDED(hr), "QI(IThreadPoolTimerStatics)", hr);
    if (FAILED(hr)) goto done;

    handler = create_handler();
    failures += check(handler != NULL, "create elapsed handler", E_FAIL);
    if (!handler) goto done;
    hr = statics->lpVtbl->CreateTimer(statics, &handler->iface, duration, &timer);
    failures += check(SUCCEEDED(hr) && timer != NULL, "CreateTimer", hr);
    if (SUCCEEDED(hr))
    {
        hr = timer->lpVtbl->get_Period(timer, &period);
        failures += check(SUCCEEDED(hr) && period.Duration == 0, "one-shot Period", hr);
        hr = timer->lpVtbl->get_Delay(timer, &delay);
        failures += check(SUCCEEDED(hr) && delay.Duration == duration.Duration, "one-shot Delay", hr);
        failures += check(WaitForSingleObject(handler->event, 1000) == WAIT_OBJECT_0,
                "one-shot callback", S_OK);
        Sleep(100);
        failures += check(handler->calls == 1, "one-shot callback count", S_OK);
        timer->lpVtbl->Release(timer);
        timer = NULL;
    }
    handler->iface.lpVtbl->Release(&handler->iface);

    handler = create_handler();
    failures += check(handler != NULL, "create periodic handler", E_FAIL);
    if (!handler) goto done;
    hr = statics->lpVtbl->CreatePeriodicTimer(statics, &handler->iface, duration, &timer);
    failures += check(SUCCEEDED(hr) && timer != NULL, "CreatePeriodicTimer", hr);
    if (SUCCEEDED(hr))
    {
        hr = timer->lpVtbl->get_Period(timer, &period);
        failures += check(SUCCEEDED(hr) && period.Duration == duration.Duration, "periodic Period", hr);
        failures += check(WaitForSingleObject(handler->event, 1000) == WAIT_OBJECT_0,
                "periodic callback", S_OK);
        Sleep(80);
        calls = handler->calls;
        failures += check(calls >= 3, "periodic callback count", S_OK);
        timer->lpVtbl->Cancel(timer);
        failures += check(wait_value(&handler->refs, 1), "periodic callbacks drained", S_OK);
        calls = handler->calls;
        Sleep(100);
        failures += check(handler->calls == calls, "periodic cancellation", S_OK);
        timer->lpVtbl->Release(timer);
        timer = NULL;
    }
    handler->iface.lpVtbl->Release(&handler->iface);

    handler = create_handler();
    destroyed = create_handler();
    failures += check(handler && destroyed, "create completion handlers", E_FAIL);
    if (handler && destroyed)
    {
        hr = statics->lpVtbl->CreateTimerWithCompletion(statics, &handler->iface, duration,
                &destroyed->iface, &timer);
        failures += check(SUCCEEDED(hr) && timer != NULL, "CreateTimerWithCompletion", hr);
        if (SUCCEEDED(hr))
        {
            failures += check(WaitForSingleObject(handler->event, 1000) == WAIT_OBJECT_0,
                    "completion timer elapsed", S_OK);
            timer->lpVtbl->Release(timer);
            timer = NULL;
            failures += check(WaitForSingleObject(destroyed->event, 1000) == WAIT_OBJECT_0,
                    "TimerDestroyedHandler", S_OK);
        }
    }
    if (handler) handler->iface.lpVtbl->Release(&handler->iface);
    if (destroyed) destroyed->iface.lpVtbl->Release(&destroyed->iface);

    failures += lifetime_tests(statics);

done:
    if (timer) timer->lpVtbl->Release(timer);
    if (statics) statics->lpVtbl->Release(statics);
    if (factory) IActivationFactory_Release(factory);
    if (classid) WindowsDeleteString(classid);
    RoUninitialize();
    printf("threadpool-timer: %u checks, %d failures: %s\n", checks, failures, failures ? "FAIL" : "PASS");
    return failures != 0;
}
