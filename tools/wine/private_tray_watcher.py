#!/usr/bin/env python3
"""Minimal SNI registry for the owned-display tray regression, never the host bus."""
import os
import sys
import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

INTERFACE = 'org.kde.StatusNotifierWatcher'


class Watcher(dbus.service.Object):
    def __init__(self, bus):
        self.items = []
        self.name = dbus.service.BusName(INTERFACE, bus=bus)
        super().__init__(bus, '/StatusNotifierWatcher')

    @dbus.service.method(INTERFACE, in_signature='s', out_signature='', sender_keyword='sender')
    def RegisterStatusNotifierItem(self, service, sender=None):
        item = str(sender) + str(service) if service.startswith('/') else str(service) + '/StatusNotifierItem'
        if item not in self.items:
            self.items.append(item)
            self.StatusNotifierItemRegistered(item)

    @dbus.service.method(INTERFACE, in_signature='s', out_signature='')
    def RegisterStatusNotifierHost(self, service):
        pass

    @dbus.service.signal(INTERFACE, signature='s')
    def StatusNotifierItemRegistered(self, item):
        pass

    @dbus.service.method('org.freedesktop.DBus.Properties', in_signature='ss', out_signature='v')
    def Get(self, interface, name):
        return self.GetAll(interface)[name]

    @dbus.service.method('org.freedesktop.DBus.Properties', in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface):
        if interface != INTERFACE: raise ValueError(interface)
        return {'RegisteredStatusNotifierItems': dbus.Array(self.items, signature='s'),
                'IsStatusNotifierHostRegistered': dbus.Boolean(True), 'ProtocolVersion': dbus.Int32(0)}


if __name__ == '__main__':
    if os.environ.get('GHUB_TRAY_PRIVATE_LAB') != '1' or len(sys.argv) != 2:
        raise SystemExit('Owned regression bus required')
    DBusGMainLoop(set_as_default=True)
    watcher = Watcher(dbus.bus.BusConnection(sys.argv[1]))
    print('READY', flush=True)
    GLib.MainLoop().run()
