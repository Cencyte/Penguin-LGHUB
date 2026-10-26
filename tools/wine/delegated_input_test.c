/* Headless spatial input delegation regression. Compile x64 MinGW with -municode.
 * Input uses Wine10.20's driver-to-server hardware path (CallHwndParam 24),
 * not PostMessage. The posted-message control verifies that distinction.
 * No system input or live desktop interaction: run on Xvfb in a copied prefix.
 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

typedef ULONG_PTR (WINAPI *callback_fn)(const MSG *, void *);
typedef BOOL (WINAPI *delegate_fn)(DWORD, callback_fn, void *, HWND, UINT);
typedef BOOL (WINAPI *undelegate_fn)(HWND, UINT);
typedef BOOL (WINAPI *handle_fn)(const MSG *, UINT);
typedef ULONG_PTR (WINAPI *hwcall_fn)(HWND, ULONG_PTR, DWORD);
static delegate_fn delegate_input;
static undelegate_fn undelegate_input;
static handle_fn handle_input;
static hwcall_fn hwcall;
static LONG checks, failures;
#define CHECK(c) do { InterlockedIncrement(&checks); if (!(c)) { InterlockedIncrement(&failures); \
    fprintf(stderr,"FAIL line %u: %s error=%lu\n",__LINE__,#c,GetLastError()); fflush(stderr); } } while (0)
#define FAILS(call, error) do { SetLastError(0); CHECK(!(call)); CHECK(GetLastError()==(error)); } while (0)

struct worker {
    HANDLE thread, ready, command, done;
    DWORD tid;
    HWND windows[2];
    volatile LONG operation, count;
    MSG messages[1024];
};
static MSG callbacks[1024];
static LONG callback_count;
static DWORD callback_tid;
static UINT callback_mode = 1;
static HWND callback_window;
static unsigned callback_operation;
static int callback_context;

static void pump(void)
{
    MSG msg;
    while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
}

static ULONG_PTR WINAPI callback(const MSG *msg, void *context)
{
    CHECK(GetCurrentThreadId() == callback_tid);
    CHECK(context == &callback_context || context == NULL);
    CHECK(msg->hwnd == callback_window || callback_window == NULL);
    CHECK(msg->message >= WM_POINTERUPDATE && msg->message <= WM_POINTERLEAVE);
    CHECK(callback_count < 1024);
    if (callback_count < 1024) callbacks[callback_count++] = *msg;
    if (callback_operation == 1) {
        MSG forged = *msg;
        forged.time ^= 0x10000000;
        FAILS(handle_input(&forged,1),ERROR_INVALID_PARAMETER);
        CHECK(handle_input(msg,1)); /* Current message waits for callback return. */
    } else if (callback_operation == 2) {
        CHECK(undelegate_input(msg->hwnd,2));
    } else if (callback_operation == 3) {
        callback_operation = 0;
        pump(); /* Exercise recursive delivery without reusing callback buffers. */
    }
    return callback_mode;
}

static DWORD WINAPI worker_main(void *arg)
{
    struct worker *w = arg;
    MSG msg;
    BOOL running = TRUE, pumping = TRUE;
    w->windows[0] = CreateWindowW(L"STATIC",L"delegate-owner",0,0,0,0,0,HWND_MESSAGE,NULL,NULL,NULL);
    w->windows[1] = CreateWindowW(L"STATIC",L"delegate-owner2",0,0,0,0,0,HWND_MESSAGE,NULL,NULL,NULL);
    CHECK(w->windows[0] && w->windows[1]);
    SetEvent(w->ready);
    while (running) {
        DWORD wait = pumping ? MsgWaitForMultipleObjects(1,&w->command,FALSE,INFINITE,QS_ALLINPUT)
                             : WaitForSingleObject(w->command,INFINITE);
        if (wait == WAIT_OBJECT_0) {
            switch (w->operation) {
            case 1: pumping=FALSE; break;
            case 2: pumping=TRUE; break;
            case 3: DestroyWindow(w->windows[0]); w->windows[0]=NULL; break;
            case 4: running=FALSE; break;
            }
        }
        if (pumping && running) while (PeekMessageW(&msg,NULL,0,0,PM_REMOVE)) {
            if (msg.message>=WM_POINTERUPDATE && msg.message<=WM_POINTERLEAVE) {
                LONG count=w->count;
                if (count<1024) w->messages[count]=msg;
                InterlockedIncrement(&w->count);
            }
            DispatchMessageW(&msg);
        }
        if (wait==WAIT_OBJECT_0) SetEvent(w->done);
    }
    if (w->windows[0]) DestroyWindow(w->windows[0]);
    DestroyWindow(w->windows[1]);
    return 0;
}

static void start_worker(struct worker *w)
{
    memset(w,0,sizeof(*w));
    w->ready=CreateEventW(NULL,FALSE,FALSE,NULL);
    w->command=CreateEventW(NULL,FALSE,FALSE,NULL);
    w->done=CreateEventW(NULL,FALSE,FALSE,NULL);
    w->thread=CreateThread(NULL,0,worker_main,w,0,&w->tid);
    CHECK(w->thread!=NULL);
    CHECK(WaitForSingleObject(w->ready,10000)==WAIT_OBJECT_0);
}
static void command(struct worker *w,LONG operation)
{
    w->operation=operation;
    SetEvent(w->command);
    CHECK(WaitForSingleObject(w->done,10000)==WAIT_OBJECT_0);
}
static void stop_worker(struct worker *w)
{
    command(w,4);
    CHECK(WaitForSingleObject(w->thread,10000)==WAIT_OBJECT_0);
    CloseHandle(w->thread);CloseHandle(w->ready);CloseHandle(w->command);CloseHandle(w->done);
}
static void inject(HWND window,WORD id)
{
    INPUT input={0};
    struct { UINT flags; const INPUT *input; LPARAM lparam; } params={0,&input,0};
    input.type=INPUT_HARDWARE;
    input.hi.uMsg=WM_POINTERENTER; /* No contact timer or mouse-promotion side effects. */
    input.hi.wParamL=id;
    input.hi.wParamH=0;
    CHECK(hwcall(window,(ULONG_PTR)&params,24)==0); /* returns NTSTATUS */
}
static void collect(struct worker *w)
{
    pump();
    command(w,2); /* Synchronize after callback replies have resumed owner delivery. */
}
static void reset_counts(struct worker *w)
{
    collect(w);
    w->count=0;callback_count=0;callback_operation=0;
}

int wmain(int argc,WCHAR **argv)
{
    HMODULE user=GetModuleHandleW(L"user32.dll"),ui=LoadLibraryW(L"win32u.dll");
    struct worker owner,delegate;
    HWND own,stale;
    MSG bad={0};
    DWORD before,after,exit_code=STILL_ACTIVE;
    WCHAR path[MAX_PATH],child_command[2*MAX_PATH];
    STARTUPINFOW startup={0};
    PROCESS_INFORMATION child;
    unsigned i;
    delegate_input=(delegate_fn)GetProcAddress(user,"DelegateInput");
    undelegate_input=(undelegate_fn)GetProcAddress(user,"UndelegateInput");
    handle_input=(handle_fn)GetProcAddress(user,"HandleDelegatedInput");
    hwcall=(hwcall_fn)GetProcAddress(ui,"NtUserCallHwndParam");
    if (!delegate_input||!undelegate_input||!handle_input||!hwcall) return 2;
    callback_tid=GetCurrentThreadId();
    own=CreateWindowW(L"STATIC",L"delegate-main",0,0,0,0,0,HWND_MESSAGE,NULL,NULL,NULL);
    if (argc>1 && !wcscmp(argv[1],L"--foreign")) {
        HWND foreign=(HWND)(UINT_PTR)wcstoull(argv[2],NULL,16);
        DWORD tid=wcstoul(argv[3],NULL,16);
        FAILS(delegate_input(0,callback,NULL,foreign,0x1000),ERROR_ACCESS_DENIED);
        FAILS(delegate_input(tid,callback,NULL,own,0x1000),ERROR_ACCESS_DENIED);
        FAILS(undelegate_input(foreign,1),ERROR_ACCESS_DENIED);
        DestroyWindow(own);
        printf("foreign delegation: %ld checks, %ld failures\n",checks,failures);
        return !!failures;
    }
    start_worker(&owner);
    if (argc>1 && !wcscmp(argv[1],L"--disabled")) {
        FAILS(delegate_input(0,callback,&callback_context,owner.windows[0],0x1000),ERROR_CALL_NOT_IMPLEMENTED);
        stop_worker(&owner);DestroyWindow(own);
        printf("disabled delegation: %ld checks, %ld failures\n",checks,failures);return !!failures;
    }
    CHECK((void *)delegate_input==(void *)GetProcAddress(user,MAKEINTRESOURCEA(2503)));
    CHECK((void *)undelegate_input==(void *)GetProcAddress(user,MAKEINTRESOURCEA(2504)));
    CHECK((void *)handle_input==(void *)GetProcAddress(user,MAKEINTRESOURCEA(2505)));
    FAILS(delegate_input(0,callback,NULL,own,0x1000),ERROR_ACCESS_DENIED);
    FAILS(delegate_input(0,callback,NULL,(HWND)0xdead,0x1000),ERROR_INVALID_WINDOW_HANDLE);
    FAILS(delegate_input(0,callback,NULL,owner.windows[0],0),ERROR_INVALID_PARAMETER);
    FAILS(delegate_input(0,callback,NULL,owner.windows[0],1),ERROR_INVALID_PARAMETER);
    FAILS(delegate_input(0,callback,NULL,owner.windows[0],2),ERROR_NOT_SUPPORTED);
    FAILS(delegate_input(0,NULL,NULL,owner.windows[0],0x1000),ERROR_INVALID_PARAMETER);
    FAILS(delegate_input(0x12345678,callback,NULL,owner.windows[0],0x1000),ERROR_INVALID_PARAMETER);
    FAILS(handle_input(&bad,1),ERROR_ACCESS_DENIED);
    FAILS(undelegate_input(owner.windows[0],1),ERROR_INVALID_PARAMETER);
    CHECK(delegate_input(0,callback,NULL,owner.windows[0],0x1000));
    FAILS(delegate_input(0,callback,NULL,owner.windows[0],0x1000),ERROR_ALREADY_REGISTERED);
    CHECK(undelegate_input(owner.windows[0],1));
    callback_window=owner.windows[0];
    CHECK(delegate_input(0,callback,&callback_context,callback_window,0x1000));
    GetModuleFileNameW(NULL,path,MAX_PATH);
    swprintf(child_command,sizeof(child_command)/sizeof(*child_command),L"\"%ls\" --foreign %llx %lx",
             path,(unsigned long long)(UINT_PTR)callback_window,callback_tid);
    startup.cb=sizeof(startup);
    if (CreateProcessW(NULL,child_command,NULL,NULL,FALSE,0,NULL,NULL,&startup,&child)) {
        CHECK(WaitForSingleObject(child.hProcess,15000)==WAIT_OBJECT_0);
        CHECK(GetExitCodeProcess(child.hProcess,&exit_code) && exit_code==0);
        if (exit_code==STILL_ACTIVE) TerminateProcess(child.hProcess,1);
        CloseHandle(child.hThread);CloseHandle(child.hProcess);
    } else CHECK(FALSE);
    FAILS(delegate_input(0,callback,&callback_context,callback_window,0x1000),ERROR_ACCESS_DENIED);
    FAILS(undelegate_input(callback_window,3),ERROR_INVALID_PARAMETER);
    FAILS(handle_input(&bad,0),ERROR_INVALID_PARAMETER);
    FAILS(handle_input((void *)1,1),ERROR_NOACCESS);
    CHECK(handle_input((void *)1,3));
    callback_operation=1;callback_mode=1;
    inject(callback_window,101);collect(&owner);
    CHECK(callback_count==1 && owner.count==1);
    CHECK(owner.messages[0].wParam==101 && callbacks[0].wParam==101);
    CHECK(owner.messages[0].hwnd==callback_window);
    CHECK(owner.messages[0].time==callbacks[0].time);
    reset_counts(&owner);callback_mode=2;
    inject(callback_window,102);collect(&owner);
    CHECK(callback_count==1 && owner.count==0);
    reset_counts(&owner);callback_mode=3;
    inject(callback_window,103);inject(callback_window,104);collect(&owner);
    CHECK(callback_count==2 && owner.count==0);
    bad=callbacks[1];bad.wParam=999;
    FAILS(handle_input(&bad,1),ERROR_INVALID_PARAMETER);
    CHECK(handle_input(&callbacks[1],1));collect(&owner);
    CHECK(owner.count==2 && owner.messages[0].wParam==103 && owner.messages[1].wParam==104);
    FAILS(handle_input(&callbacks[1],1),ERROR_INVALID_PARAMETER);
    reset_counts(&owner);
    inject(callback_window,105);collect(&owner);
    CHECK(handle_input(&callbacks[0],2));collect(&owner);
    CHECK(owner.count==0);
    reset_counts(&owner);callback_mode=1;
    CHECK(PostMessageW(callback_window,WM_POINTERENTER,106,0));collect(&owner);
    CHECK(callback_count==0 && owner.count==1); /* posted input is not hardware */
    reset_counts(&owner);callback_operation=3;
    inject(callback_window,107);inject(callback_window,108);collect(&owner);
    CHECK(callback_count==2 && owner.count==2);
    reset_counts(&owner);callback_operation=2;
    inject(callback_window,109);inject(callback_window,110);collect(&owner);
    CHECK(callback_count==1 && owner.count==0); /* unregister inside callback cancels queued callback */
    reset_counts(&owner);callback_mode=3;
    CHECK(delegate_input(0,callback,&callback_context,callback_window,0x1000));
    inject(callback_window,111);collect(&owner);
    CHECK(undelegate_input(callback_window,1));collect(&owner);
    CHECK(owner.count==1 && owner.messages[0].wParam==111);
    reset_counts(&owner);
    CHECK(delegate_input(0,callback,&callback_context,callback_window,0x1000));
    inject(callback_window,112); /* unregister before callback delivery */
    CHECK(undelegate_input(callback_window,2));collect(&owner);
    CHECK(callback_count==0 && owner.count==0);
    reset_counts(&owner);command(&owner,1);
    inject(callback_window,113); /* input queued before registration */
    CHECK(delegate_input(0,callback,&callback_context,callback_window,0x1000));
    callback_mode=1;collect(&owner);
    CHECK(callback_count==1 && owner.count==1);
    CHECK(undelegate_input(callback_window,2));
    reset_counts(&owner);callback_window=NULL;
    CHECK(delegate_input(0,callback,&callback_context,owner.windows[0],0x1000));
    CHECK(delegate_input(0,callback,&callback_context,owner.windows[1],0x1000));
    inject(owner.windows[0],114);inject(owner.windows[1],115);collect(&owner);
    CHECK(callback_count==2 && owner.count==2);
    CHECK(undelegate_input(owner.windows[0],2));CHECK(undelegate_input(owner.windows[1],2));
    reset_counts(&owner);
    CHECK(delegate_input(0,callback,&callback_context,owner.windows[0],0x1000));
    inject(owner.windows[0],116);stale=owner.windows[0];command(&owner,3);collect(&owner);
    CHECK(callback_count==0 && owner.count==0);
    FAILS(undelegate_input(stale,1),ERROR_INVALID_WINDOW_HANDLE);
    CHECK(delegate_input(0,callback,&callback_context,owner.windows[1],0x1000));
    callback_mode=3;inject(owner.windows[1],117);collect(&owner);
    stop_worker(&owner);pump();
    FAILS(handle_input(&callbacks[0],1),ERROR_ACCESS_DENIED);
    start_worker(&owner);start_worker(&delegate);command(&delegate,1);
    CHECK(delegate_input(delegate.tid,callback,&callback_context,owner.windows[0],0x1000));
    inject(owner.windows[0],118);stop_worker(&delegate);collect(&owner);
    CHECK(owner.count==1 && owner.messages[0].wParam==118); /* dead delegate resumes input */
    reset_counts(&owner);start_worker(&delegate);command(&delegate,1);
    callback_tid=delegate.tid;callback_mode=3;
    CHECK(delegate_input(delegate.tid,callback,&callback_context,owner.windows[0],0x1000));
    inject(owner.windows[0],119);command(&delegate,2);command(&owner,2);
    CHECK(callback_count==1 && owner.count==0);
    stop_worker(&delegate);collect(&owner);
    CHECK(owner.count==1 && owner.messages[0].wParam==119); /* also resolve already-deferred input */
    callback_tid=GetCurrentThreadId();
    CHECK(delegate_input(0,callback,&callback_context,owner.windows[0],0x1000));
    CHECK(undelegate_input(owner.windows[0],2));
    CHECK(GetProcessHandleCount(GetCurrentProcess(),&before));
    for(i=0;i<128;++i) {
        CHECK(delegate_input(0,callback,&callback_context,owner.windows[0],0x1000));
        inject(owner.windows[0],200+i);
        CHECK(undelegate_input(owner.windows[0],2));
    }
    CHECK(GetProcessHandleCount(GetCurrentProcess(),&after));CHECK(before==after);
    stop_worker(&owner);DestroyWindow(own);
    printf("delegation: %ld checks, %ld failures\n",checks,failures);
    return !!failures;
}
