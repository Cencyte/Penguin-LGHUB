"""Temporary KWin focus subscription. No input interception or persistent config."""
import json
import os
from pathlib import Path
import secrets
import time

from profile_dbus import Bus, BusError

SCRIPT = '''
var sequence = 0;
function report() {
    var w = workspace.activeWindow;
    callDBus(DESTINATION, "/Focus", "org.penguinLGHUB.Focus", "Observe",
        JSON.stringify({epoch: EPOCH, sequence: ++sequence, pid: w && w.pid > 0 ? w.pid : 0,
            window: w ? String(w.internalId) : "",
            desktop: w ? w.desktopWindow : false, dock: w ? w.dock : false}));
}
workspace.windowActivated.connect(report);
report();
var heartbeat = new QTimer(); heartbeat.interval = 500;
heartbeat.timeout.connect(function () {
    callDBus("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
        "NameHasOwner", DESTINATION, function (alive) {
            if (alive) { report(); return; }
            heartbeat.stop(); workspace.windowActivated.disconnect(report);
            callDBus("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "unloadScript", PLUGIN);
        });
});
heartbeat.start();
'''


class KWin:
    def __init__(self, directory, bus_factory=Bus):
        self.directory = directory
        self.bus = bus_factory()
        self.plugin = 'penguin-focus-' + secrets.token_hex(12)
        self.path = directory / 'kwin-focus.js'
        self.owner = None; self.epoch = ''; self.sequence = 0
        self.last_event = 0; self.last_refresh = 0; self.last_attempt = 0
        self.focus = None; self.lock_owner = None; self.locked = True
        self.loaded = False

    def unload(self):
        if self.loaded and self.owner:
            try:
                self.bus.call(self.owner, '/Scripting', 'org.kde.kwin.Scripting', 'unloadScript', self.plugin)
            except BusError:
                pass
        self.loaded = False; self.focus = None

    def install(self, owner):
        # Confirm this is our user's Wayland compositor, not another X11/xrdp bus.
        pid = self.bus.call('org.freedesktop.DBus', '/org/freedesktop/DBus',
                            'org.freedesktop.DBus', 'GetConnectionUnixProcessID', owner)[0]
        process = Path('/proc') / str(pid)
        if process.stat().st_uid != os.getuid() or Path(os.readlink(process/'exe')).name != 'kwin_wayland':
            raise RuntimeError('Session bus does not belong to this user\'s Wayland KWin')
        self.epoch = secrets.token_hex(16); self.sequence = 0
        self.path.write_text(SCRIPT.replace('DESTINATION', json.dumps(self.bus.name))
                             .replace('EPOCH', json.dumps(self.epoch)).replace('PLUGIN', json.dumps(self.plugin)))
        self.path.chmod(0o600)
        script = self.bus.call(owner, '/Scripting', 'org.kde.kwin.Scripting', 'loadScript', str(self.path), self.plugin)[0]
        if script < 0:
            raise BusError('Cannot load the temporary KWin focus script')
        self.loaded = True
        self.bus.call(owner, '/Scripting/Script'+str(script), 'org.kde.kwin.Script', 'run')
        self.last_event = time.monotonic()

    def refresh(self):
        now = time.monotonic()
        if now - self.last_refresh < 1:
            return
        self.last_refresh = now
        owner = self.bus.owner('org.kde.KWin')
        if owner != self.owner:
            self.unload(); self.owner = owner; self.last_attempt = 0
        if self.loaded and now - self.last_event > 3:
            self.unload()
        if owner and not self.loaded and now - self.last_attempt > 2:
            self.last_attempt = now
            try:
                self.install(owner)
            except BusError:
                self.unload()
        lock_owner = self.bus.owner('org.freedesktop.ScreenSaver')
        self.lock_owner = lock_owner
        if lock_owner:
            # KDE exports both standard and historical paths; support either.
            self.locked = True
            for path in ('/ScreenSaver', '/org/freedesktop/ScreenSaver'):
                try:
                    self.locked = self.bus.call(lock_owner, path, 'org.freedesktop.ScreenSaver', 'GetActive')[0]
                    break
                except BusError:
                    pass
        else:
            self.locked = False  # e.g. private --no-lockscreen KWin tests.

    def accept(self, sender, payload):
        if sender != self.owner or not self.loaded or not isinstance(payload, str) or len(payload) > 8192:
            return False
        try:
            event = json.loads(payload)
            if (not isinstance(event, dict) or event.get('epoch') != self.epoch
                    or type(event.get('sequence')) is not int or event['sequence'] <= self.sequence
                    or type(event.get('pid')) is not int or not 0 <= event['pid'] <= 0x7fffffff
                    or not isinstance(event.get('window'), str) or len(event['window']) > 128
                    or type(event.get('desktop')) is not bool or type(event.get('dock')) is not bool):
                return False
        except (ValueError, TypeError):
            return False
        self.sequence = event['sequence']; self.last_event = time.monotonic()
        self.focus = (event['window'], 0 if event['desktop'] or event['dock'] else event['pid'])
        return True

    def foreground(self):
        self.refresh()
        if self.locked or not self.loaded or time.monotonic() - self.last_event > 3:
            return None  # Pause; missing compositor/locked is not Desktop focus.
        return self.focus

    def wait(self, timeout):
        for message in self.bus.receive(timeout):
            try:
                m = self.bus.metadata(message)
                if (m['interface'], m['member'], m['path']) == ('org.penguinLGHUB.Focus', 'Observe', '/Focus'):
                    try:
                        args = self.bus.read(message)
                        accepted = len(args) == 1 and self.accept(m['sender'], args[0])
                    except BusError:
                        accepted = False
                    self.bus.reply(message, accepted)
                elif m['interface'] == 'org.freedesktop.ScreenSaver' and m['member'] == 'ActiveChanged' and m['sender'] == self.lock_owner:
                    args = self.bus.read(message)
                    if len(args) == 1 and type(args[0]) is bool:
                        self.locked = args[0]
                elif m['interface'] == 'org.freedesktop.DBus' and m['member'] == 'NameOwnerChanged' and m['sender'] == 'org.freedesktop.DBus':
                    args = self.bus.read(message)
                    if args and args[0] in ('org.kde.KWin', 'org.freedesktop.ScreenSaver'):
                        self.focus = None; self.last_refresh = 0
            finally:
                self.bus.lib.dbus_message_unref(message)

    def close(self):
        try:
            self.unload()
            self.path.unlink(missing_ok=True)
        finally:
            self.bus.close()
