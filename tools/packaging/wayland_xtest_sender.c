/* XTEST sender for the isolated KWin feasibility probe, never a desktop tool. */
#include <X11/Xlib.h>
#include <X11/keysym.h>
#include <X11/extensions/XTest.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(void) {
    const char *guard = getenv("PENGUIN_PRIVATE_WAYLAND_PROBE");
    if (!guard || strcmp(guard, "1")) return 2;
    Display *dpy = XOpenDisplay(NULL);
    if (!dpy) return 3;
    const KeySym keys[] = {XK_a, XK_a, XK_Control_R, XK_a,
                          XK_a, XK_Control_R, XK_F12, XK_F12};
    const int down[] = {1, 0, 1, 1, 0, 0, 1, 0};
    for (unsigned i = 0; i < sizeof(keys)/sizeof(keys[0]); ++i) {
        KeyCode code = XKeysymToKeycode(dpy, keys[i]);
        if (!code || !XTestFakeKeyEvent(dpy, code, down[i], 0)) return 4;
        XFlush(dpy);
        usleep(50000);
    }
    XSync(dpy, False);
    usleep(500000);
    XCloseDisplay(dpy);
    return 0;
}
