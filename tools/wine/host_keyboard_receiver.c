/* Native X11 receiver for a private Xvfb display, never the user's session. */
#include <X11/Xlib.h>
#include <X11/keysym.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/select.h>

int main(void)
{
    if (!getenv("GHUB_HOST_INPUT_PRIVATE_TEST")) return 2;
    Display *d = XOpenDisplay(NULL);
    if (!d) return 2;
    Window w = XCreateSimpleWindow(d,DefaultRootWindow(d),0,0,400,200,0,0,0);
    XSelectInput(d,w,KeyPressMask|KeyReleaseMask|StructureNotifyMask);
    XMapWindow(d,w); XSync(d,False);
    XSetInputFocus(d,w,RevertToParent,CurrentTime); XSync(d,False);
    setvbuf(stdout,NULL,_IONBF,0); puts("READY");
    for (;;)
    {
        while (XPending(d))
        {
            XEvent e; XNextEvent(d,&e);
            if (e.type == KeyPress || e.type == KeyRelease)
                printf("KEY %s %lx %x\n",e.type == KeyPress ? "down" : "up",
                       XLookupKeysym(&e.xkey,0),e.xkey.state);
        }
        fd_set readfds; FD_ZERO(&readfds); FD_SET(0,&readfds); FD_SET(ConnectionNumber(d),&readfds);
        if (select(ConnectionNumber(d)+1,&readfds,NULL,NULL,NULL)<0) return 2;
        if (FD_ISSET(0,&readfds)) break;
    }
    XDestroyWindow(d,w); XCloseDisplay(d); return 0;
}
