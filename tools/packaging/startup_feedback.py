"""Finish the desktop's startup notification without changing cursor themes.

The desktop starts the busy indicator for StartupNotify=true. We acknowledge
its X11 ID through the freedesktop protocol. Plasma Wayland manages its own
activation feedback; an XDG activation token must not be used as an X11 ID.
No input, focus change, or visible helper window is required.
"""
import ctypes as c
import sys


class MessageData(c.Union):
    _fields_ = [('bytes', c.c_char * 20), ('longs', c.c_long * 5)]


class ClientMessage(c.Structure):
    _fields_ = [('type', c.c_int), ('serial', c.c_ulong), ('send_event', c.c_int),
                ('display', c.c_void_p), ('window', c.c_ulong), ('message_type', c.c_ulong),
                ('format', c.c_int), ('data', MessageData)]


class Event(c.Union):
    _fields_ = [('client', ClientMessage), ('pad', c.c_long * 24)]


def message_chunks(identifier):
    if not identifier or len(identifier.encode()) > 4096 or '\0' in identifier:
        raise ValueError('Invalid desktop startup identifier')
    escaped = identifier.replace('\\', '\\\\').replace('"', '\\"')
    message = ('remove: ID="' + escaped + '"\0').encode()
    return [message[i:i+20] for i in range(0, len(message), 20)]


def finish(display, identifier):
    chunks = message_chunks(identifier)
    x = c.CDLL('libX11.so.6')
    D, U, I = c.c_void_p, c.c_ulong, c.c_int
    signatures = {
        'XOpenDisplay': (D, [c.c_char_p]), 'XDefaultRootWindow': (U, [D]),
        'XInternAtom': (U, [D, c.c_char_p, I]),
        'XCreateSimpleWindow': (U, [D, U, I, I, c.c_uint, c.c_uint, c.c_uint, U, U]),
        'XSendEvent': (I, [D, U, I, c.c_long, c.POINTER(Event)]),
        'XSync': (I, [D, I]), 'XDestroyWindow': (I, [D, U]), 'XCloseDisplay': (I, [D]),
    }
    for name, (result, args) in signatures.items():
        function = getattr(x, name); function.restype = result; function.argtypes = args
    d = x.XOpenDisplay(display.encode())
    if not d:
        raise OSError('Cannot complete startup notification on this display')
    window = None
    try:
        root = x.XDefaultRootWindow(d)
        window = x.XCreateSimpleWindow(d, root, 0, 0, 1, 1, 0, 0, 0)
        for index, chunk in enumerate(chunks):
            event = Event()
            event.client.type = 33; event.client.display = d; event.client.window = window
            event.client.message_type = x.XInternAtom(d, b'_NET_STARTUP_INFO' if index else b'_NET_STARTUP_INFO_BEGIN', 0)
            event.client.format = 8; event.client.data.bytes = chunk
            if not x.XSendEvent(d, root, 0, 1 << 22, c.byref(event)):
                raise OSError('Startup notification was not sent')
        x.XSync(d, 0)
    finally:
        if window: x.XDestroyWindow(d, window)
        x.XCloseDisplay(d)


class Feedback:
    def __init__(self, env):
        self.identifier = env.get('DESKTOP_STARTUP_ID')
        self.display = env.get('DISPLAY')
        self.completed = not (self.identifier and self.display)

    def complete(self):
        if self.completed:
            return
        self.completed = True
        try:
            finish(self.display, self.identifier)
        except (OSError, ValueError) as error:
            print('Desktop startup feedback:', error, file=sys.stderr)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.complete()
