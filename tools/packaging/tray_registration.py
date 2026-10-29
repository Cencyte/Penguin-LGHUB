"""Minimal XEmbed host for an isolated G HUB startup check, never the live panel.

Receiving a dock request is a separate gate from frontend readiness. This does
not certify Plasma's SNI presentation, popup input or the physical GPU.
"""
import ctypes as c
import os
from pathlib import Path
import socket


class ClientMessage(c.Structure):
    _fields_ = [('type', c.c_int), ('serial', c.c_ulong), ('send_event', c.c_int),
                ('display', c.c_void_p), ('window', c.c_ulong), ('message_type', c.c_ulong),
                ('format', c.c_int), ('data', c.c_long * 5)]


class Event(c.Union):
    _fields_ = [('client', ClientMessage), ('pad', c.c_long * 24)]


class PrivateTray:
    def __init__(self, display):
        if (os.environ.get('PENGUIN_ISOLATED_UI_PROBE') != '1'
                or socket.if_nameindex() != [(1, 'lo')]
                or Path('/dev/input').exists() or list(Path('/dev').glob('hidraw*'))):
            raise RuntimeError('Tray host requires an isolated test display/network/devices')
        self.x = x = c.CDLL('libX11.so.6')
        D, U, I = c.c_void_p, c.c_ulong, c.c_int
        signatures = {
            'XOpenDisplay': (D, [c.c_char_p]), 'XDefaultRootWindow': (U, [D]),
            'XDefaultScreen': (I, [D]), 'XInternAtom': (U, [D, c.c_char_p, I]),
            'XCreateSimpleWindow': (U, [D, U, I, I, c.c_uint, c.c_uint, c.c_uint, U, U]),
            'XGetSelectionOwner': (U, [D, U]), 'XSetSelectionOwner': (I, [D, U, U, U]),
            'XPending': (I, [D]), 'XNextEvent': (I, [D, c.POINTER(Event)]),
            'XReparentWindow': (I, [D, U, U, I, I]), 'XMapWindow': (I, [D, U]),
            'XSendEvent': (I, [D, U, I, c.c_long, c.POINTER(Event)]),
            'XSync': (I, [D, I]), 'XDestroyWindow': (I, [D, U]),
            'XCloseDisplay': (I, [D]),
        }
        for name, (result, args) in signatures.items():
            f = getattr(x, name); f.restype = result; f.argtypes = args
        self.display = d = x.XOpenDisplay(display.encode())
        if not d:
            raise RuntimeError('Cannot open owned tray-test display')
        atom = lambda name: x.XInternAtom(d, name.encode(), 0)
        self.selection = atom('_NET_SYSTEM_TRAY_S' + str(x.XDefaultScreen(d)))
        if x.XGetSelectionOwner(d, self.selection):
            x.XCloseDisplay(d)
            raise RuntimeError('Refusing to replace an existing tray host')
        self.window = x.XCreateSimpleWindow(d, x.XDefaultRootWindow(d), 0, 0, 32, 32, 0, 0, 0)
        self.opcode, self.xembed = atom('_NET_SYSTEM_TRAY_OPCODE'), atom('_XEMBED')
        self.docked = set()
        x.XSetSelectionOwner(d, self.selection, self.window, 0)
        x.XSync(d, 0)
        if x.XGetSelectionOwner(d, self.selection) != self.window:
            self.close()
            raise RuntimeError('Could not claim owned test tray selection')

    def poll(self):
        x, d = self.x, self.display
        while x.XPending(d):
            event = Event(); x.XNextEvent(d, c.byref(event))
            m = event.client
            if m.type != 33 or m.message_type != self.opcode or m.format != 32 or m.data[1] != 0:
                continue
            child = m.data[2]
            x.XReparentWindow(d, child, self.window, 0, 0)
            embedded = Event()
            embedded.client.type = 33; embedded.client.display = d
            embedded.client.window = child; embedded.client.message_type = self.xembed
            embedded.client.format = 32; embedded.client.data[3] = self.window
            x.XSendEvent(d, child, 0, 0, c.byref(embedded))
            x.XMapWindow(d, child); x.XSync(d, 0)
            self.docked.add(child)
        return len(self.docked)

    def close(self):
        if self.display:
            self.x.XDestroyWindow(self.display, self.window)
            self.x.XCloseDisplay(self.display)
            self.display = None
