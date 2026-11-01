import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools/wine'))
from profile_kwin import KWin, BusError


class KWinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bus = MagicMock(); self.bus.name = ':1.50'
        self.observer = KWin(Path(self.tmp.name), bus_factory=lambda:self.bus)
        self.observer.owner = ':1.20'; self.observer.epoch = 'current'
        self.observer.loaded = True; self.observer.locked = False
        self.observer.last_refresh = time.monotonic()

    def tearDown(self):
        self.observer.close(); self.tmp.cleanup()

    def event(self, **changes):
        data = dict(epoch='current',sequence=1,pid=20,window='uuid',desktop=False,dock=False)
        data.update(changes)
        return json.dumps(data)

    def test_protected_compositor_uses_bus_credentials_without_procfs(self):
        w = self.observer
        self.bus.call.side_effect = [(os.getuid(),), ('KWin\nOperation Mode: Wayland\n',), (7,), ()]
        with patch('os.readlink', side_effect=PermissionError(13, 'Permission denied')) as readlink:
            w.install(w.owner)
            readlink.assert_not_called()
        self.assertTrue(w.loaded)
        self.assertTrue(w.path.is_file())
        self.bus.call.assert_any_call('org.freedesktop.DBus', '/org/freedesktop/DBus',
                                     'org.freedesktop.DBus', 'GetConnectionUnixUser', w.owner)
        self.bus.call.assert_any_call(w.owner, '/KWin', 'org.kde.KWin', 'supportInformation')
        self.bus.call.assert_any_call(w.owner, '/Scripting/Script7', 'org.kde.kwin.Script', 'run')
        self.bus.call.side_effect = None

    def test_foreign_or_invalid_bus_credentials_cannot_load_script(self):
        for credentials in [(os.getuid()+1,), (True,), (), ('1000',)]:
            with self.subTest(credentials=credentials):
                self.bus.call.reset_mock()
                self.bus.call.return_value = credentials
                with self.assertRaisesRegex(RuntimeError, 'Wayland KWin'):
                    self.observer.install(self.observer.owner)
                self.assertEqual(self.bus.call.call_count, 1)
                self.assertFalse(self.observer.path.exists())

    def test_x11_or_malformed_operation_mode_cannot_load_script(self):
        for support in [('Operation Mode: X11\n',), ('missing mode',), (True,), (),
                        ('Prefix Operation Mode: Wayland\n',)]:
            with self.subTest(support=support):
                self.bus.call.reset_mock()
                self.bus.call.side_effect = [(os.getuid(),), support]
                with self.assertRaisesRegex(RuntimeError, 'Wayland KWin'):
                    self.observer.install(self.observer.owner)
                self.assertEqual(self.bus.call.call_count, 2)
                self.assertFalse(self.observer.path.exists())
        self.bus.call.side_effect = None

    def test_owner_disappearing_during_validation_retries_without_loading_script(self):
        w = self.observer
        w.loaded = False; w.last_refresh = 0
        self.bus.owner.side_effect = [w.owner, None]
        self.bus.call.side_effect = [(os.getuid(),), BusError('Owner exited')]
        w.refresh()
        self.assertFalse(w.loaded)
        self.assertIsNone(w.focus)
        self.assertEqual(self.bus.call.call_count, 2)
        self.assertFalse(w.path.exists())
        self.bus.call.side_effect = None

    def test_sender_epoch_sequence_and_schema_gate_focus(self):
        w = self.observer
        for sender, payload in [(':1.21',self.event()), (w.owner,self.event(epoch='old')),
                                (w.owner,self.event(pid=True)), (w.owner,self.event(pid=-1)),
                                (w.owner,self.event(window=[])), (w.owner,'[]'), (w.owner,'{')]:
            self.assertFalse(w.accept(sender,payload))
        self.assertTrue(w.accept(w.owner,self.event()))
        self.assertEqual(w.foreground(),('uuid',20))
        self.assertFalse(w.accept(w.owner,self.event()))

    def test_lock_staleness_and_no_compositor_pause_instead_of_desktop(self):
        w = self.observer; w.accept(w.owner,self.event())
        w.locked = True; self.assertIsNone(w.foreground())
        w.locked = False; w.last_event = time.monotonic()-4
        self.assertIsNone(w.foreground())
        w.last_event = time.monotonic(); w.loaded = False
        self.assertIsNone(w.foreground())

    def test_actual_desktop_and_dock_map_to_desktop_identity(self):
        w = self.observer
        self.assertTrue(w.accept(w.owner,self.event(desktop=True)))
        self.assertEqual(w.foreground(),('uuid',0))
        self.assertTrue(w.accept(w.owner,self.event(sequence=2,dock=True)))
        self.assertEqual(w.foreground(),('uuid',0))

    def test_owner_change_unloads_old_subscription_and_installs_fresh_one(self):
        w = self.observer; w.accept(w.owner,self.event()); w.last_refresh = 0
        self.bus.owner.side_effect = [':1.99',None]
        with patch.object(w,'install') as install:
            w.refresh()
            install.assert_called_once_with(':1.99')
        self.assertIsNone(w.focus)
        self.bus.call.assert_any_call(':1.20','/Scripting','org.kde.kwin.Scripting','unloadScript',w.plugin)

    def test_missing_heartbeat_reloads_only_our_script(self):
        w=self.observer; w.last_refresh=0; w.last_event=0
        self.bus.owner.side_effect=[w.owner,None]
        with patch.object(w,'install') as install:
            w.refresh(); install.assert_called_once_with(w.owner)
        self.assertIsNone(w.focus)


if __name__=='__main__': unittest.main()
