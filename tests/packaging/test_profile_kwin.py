import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools/wine'))
from profile_kwin import KWin


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
