"""Small private libdbus connection for the passive KWin profile observer.

Uses the desktop's existing libdbus, not Python bindings or Qt development files.
Each connection belongs to one observer thread. Only scalar messages are needed.
"""
import ctypes as C
import os


class BusError(RuntimeError):
    pass


class Bus:
    def __init__(self):
        if not os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
            raise BusError('The graphical session D-Bus address is missing')
        self.lib = d = C.CDLL('libdbus-1.so.3')
        ptr = C.c_void_p
        signatures = {
            'dbus_threads_init_default': ([], C.c_int),
            'dbus_bus_get_private': ([C.c_int, ptr], ptr),
            'dbus_bus_get_unique_name': ([ptr], C.c_char_p),
            'dbus_connection_set_exit_on_disconnect': ([ptr, C.c_int], None),
            'dbus_connection_read_write': ([ptr, C.c_int], C.c_int),
            'dbus_connection_pop_message': ([ptr], ptr),
            'dbus_connection_send_with_reply_and_block': ([ptr, ptr, C.c_int, ptr], ptr),
            'dbus_connection_send': ([ptr, ptr, ptr], C.c_int),
            'dbus_connection_flush': ([ptr], None),
            'dbus_connection_close': ([ptr], None),
            'dbus_connection_unref': ([ptr], None),
            'dbus_bus_add_match': ([ptr, C.c_char_p, ptr], None),
            'dbus_message_new_method_call': ([C.c_char_p]*4, ptr),
            'dbus_message_new_method_return': ([ptr], ptr),
            'dbus_message_unref': ([ptr], None),
            'dbus_message_get_type': ([ptr], C.c_int),
            'dbus_message_get_sender': ([ptr], C.c_char_p),
            'dbus_message_get_member': ([ptr], C.c_char_p),
            'dbus_message_get_interface': ([ptr], C.c_char_p),
            'dbus_message_get_path': ([ptr], C.c_char_p),
            'dbus_message_get_signature': ([ptr], C.c_char_p),
            'dbus_message_get_error_name': ([ptr], C.c_char_p),
            # These two are varargs; fixed prefix plus explicitly typed values.
            'dbus_message_append_args': ([ptr, C.c_int], C.c_int),
            'dbus_message_get_args': ([ptr, ptr, C.c_int], C.c_int),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(d, name); fn.argtypes = args; fn.restype = result
        if not d.dbus_threads_init_default():
            raise BusError('Cannot initialize D-Bus threading')
        self.connection = d.dbus_bus_get_private(0, None)
        if not self.connection:
            raise BusError('Cannot connect to the graphical session bus')
        d.dbus_connection_set_exit_on_disconnect(self.connection, False)
        self.name = d.dbus_bus_get_unique_name(self.connection).decode()
        d.dbus_bus_add_match(self.connection, b"type='signal',interface='org.freedesktop.ScreenSaver',member='ActiveChanged'", None)
        d.dbus_bus_add_match(self.connection, b"type='signal',interface='org.freedesktop.DBus',member='NameOwnerChanged'", None)
        d.dbus_connection_flush(self.connection)

    def append(self, message, *values):
        keep = []; args = []
        for value in values:
            if isinstance(value, str):
                obj = C.c_char_p(value.encode()); kind = ord('s')
            elif isinstance(value, bool):
                obj = C.c_uint32(value); kind = ord('b')
            elif isinstance(value, int):
                obj = C.c_int32(value); kind = ord('i')
            else:
                raise TypeError('Unsupported D-Bus argument')
            keep.append(obj); args.extend((C.c_int(kind), C.byref(obj)))
        args.append(C.c_int(0))
        if not self.lib.dbus_message_append_args(message, *args):
            raise BusError('Cannot allocate D-Bus arguments')

    def read(self, message):
        signature = self.lib.dbus_message_get_signature(message).decode()
        args = []; values = []
        for char in signature:
            cls = {'s': C.c_char_p, 'b': C.c_uint32, 'i': C.c_int32, 'u': C.c_uint32}.get(char)
            if cls is None:
                raise BusError('Unexpected D-Bus signature: ' + signature)
            obj = cls(); values.append((char, obj))
            args.extend((C.c_int(ord(char)), C.byref(obj)))
        args.append(C.c_int(0))
        if not self.lib.dbus_message_get_args(message, None, *args):
            raise BusError('Cannot read D-Bus arguments')
        return tuple(obj.value.decode() if char == 's' else bool(obj.value) if char == 'b' else obj.value
                     for char, obj in values)

    def call(self, destination, path, interface, method, *values):
        message = self.lib.dbus_message_new_method_call(*(s.encode() for s in (destination, path, interface, method)))
        if not message:
            raise BusError('Cannot allocate D-Bus call')
        try:
            self.append(message, *values)
            reply = self.lib.dbus_connection_send_with_reply_and_block(self.connection, message, 1500, None)
            if not reply:
                raise BusError('D-Bus call failed: ' + interface + '.' + method)
            try:
                if self.lib.dbus_message_get_type(reply) == 3:
                    raise BusError('D-Bus error: ' + self.lib.dbus_message_get_error_name(reply).decode())
                return self.read(reply)
            finally:
                self.lib.dbus_message_unref(reply)
        finally:
            self.lib.dbus_message_unref(message)

    def owner(self, service):
        try:
            return self.call('org.freedesktop.DBus', '/org/freedesktop/DBus',
                             'org.freedesktop.DBus', 'GetNameOwner', service)[0]
        except BusError:
            return None

    def receive(self, timeout):
        if not self.lib.dbus_connection_read_write(self.connection, int(timeout*1000)):
            raise BusError('Graphical session bus disconnected')
        # Bound work per cycle so a busy session bus cannot starve heartbeats.
        for _ in range(256):
            message = self.lib.dbus_connection_pop_message(self.connection)
            if not message:
                break
            yield message

    def metadata(self, message):
        def field(name):
            value = getattr(self.lib, 'dbus_message_get_'+name)(message)
            return value.decode() if value else ''
        return {k: field(k) for k in ('sender', 'member', 'interface', 'path')}

    def reply(self, message, accepted):
        reply = self.lib.dbus_message_new_method_return(message)
        if reply:
            try:
                self.append(reply, accepted)
                self.lib.dbus_connection_send(self.connection, reply, None)
                self.lib.dbus_connection_flush(self.connection)
            finally:
                self.lib.dbus_message_unref(reply)

    def close(self):
        if self.connection:
            self.lib.dbus_connection_close(self.connection)
            self.lib.dbus_connection_unref(self.connection)
            self.connection = None
