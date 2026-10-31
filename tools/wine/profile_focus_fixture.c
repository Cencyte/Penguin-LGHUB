/* Native fixture for an owned Xvfb. Never sends input or changes focus. */
#include <X11/Xlib.h>
#include <X11/Xatom.h>
#include <unistd.h>
int main(void)
{
    Display *d = XOpenDisplay(NULL);
    Window w;
    unsigned long pid = getpid();
    if (!d) return 2;
    w = XCreateSimpleWindow(d, DefaultRootWindow(d), 200, 100, 320, 200, 0, 0, 0xffffff);
    XStoreName(d, w, "Penguin native profile fixture");
    XChangeProperty(d, w, XInternAtom(d, "_NET_WM_PID", False), XA_CARDINAL,
                    32, PropModeReplace, (unsigned char *)&pid, 1);
    XMapWindow(d, w);
    XFlush(d);
    for (;;) { XEvent event; XNextEvent(d, &event); }
}
