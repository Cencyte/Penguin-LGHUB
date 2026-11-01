from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
import host_keyboard_threads as probe


def fixture():
    sender, receiver = [], []
    for i, name in enumerate(probe.CASES):
        start = 10000 * (i + 1)
        sender.append(f'CASE {name} {start}')
        count = probe.COUNTS[i]
        for j in range(count):
            vk, scan, up, modifier = 0xdb, 34, j % 2, 0
            if name == 'modifier-chord':
                vk, scan, up, modifier = [(0xa2, 37, 0, 0x4000000), (0x41, 38, 0, 0x4000000),
                                          (0x41, 38, 1, 0x4000000), (0xa2, 37, 1, 0)][j]
            now = start + 10 + 90*j
            sender.append(f'SEND {now} {100+j%2} {vk:x} 1a {2*up:x} 1 0')
            receiver.append(f'TIMED_KEY {now+1} {"up" if up else "down"} 5b {scan:x} {modifier:x} 0')
    sender.append('CASE end 80000')
    return '\n'.join(sender), '\n'.join(receiver)


class ThreadedInputMeasurementTests(unittest.TestCase):
    def test_matching_edges_have_measured_latency(self):
        result = probe.analyze(*fixture())
        self.assertTrue(result['passed'])
        self.assertEqual(result['cases']['cross-thread']['delivery_ms'], [1.0]*6)

    def test_successful_sendinput_does_not_mask_late_release(self):
        sender, receiver = fixture()
        receiver = receiver.replace('TIMED_KEY 20101 up', 'TIMED_KEY 20801 up')
        result = probe.analyze(sender, receiver)
        self.assertFalse(result['passed'])
        self.assertEqual(result['cases']['cross-thread']['delivery_ms'][1], 701)

    def test_thread_exit_must_not_release_key_early(self):
        sender, receiver = fixture()
        receiver = receiver.replace('TIMED_KEY 60101 up', 'TIMED_KEY 60012 up')
        self.assertFalse(probe.analyze(sender, receiver)['cases']['worker-exit']['passed'])

    def test_repeats_allowed_during_hold_but_not_after_release_or_on_taps(self):
        sender, receiver = fixture()
        repeat = '\nTIMED_KEY {} down 5b 22 0 1'
        self.assertTrue(probe.analyze(sender, receiver + repeat.format(70080))['passed'])
        self.assertFalse(probe.analyze(sender, receiver + repeat.format(70800))['passed'])
        self.assertFalse(probe.analyze(sender, receiver + repeat.format(20800))['passed'])

    def test_missing_wrong_or_still_modified_events_fail(self):
        sender, receiver = fixture()
        self.assertFalse(probe.analyze(sender, '')['passed'])
        self.assertFalse(probe.analyze('', receiver)['passed'])
        self.assertFalse(probe.analyze(sender, receiver.replace('up 5b 22', 'up 5b 23'))['passed'])
        self.assertFalse(probe.analyze(sender, receiver.replace('up 5b 25 0', 'up 5b 25 4000000'))['passed'])


class PendingPortalMeasurementTests(unittest.TestCase):
    def monitor(self, count=1):
        lines = []
        for _ in range(count):
            for interface, method in [('org.freedesktop.portal.RemoteDesktop', 'CreateSession'),
                                      ('org.freedesktop.portal.RemoteDesktop', 'Start'),
                                      ('org.freedesktop.impl.portal.RemoteDesktop', 'Start')]:
                lines.append(f'method call time=123 sender=:1.2 -> destination=:1.3 '
                             f'serial=1 path=/test; interface={interface}; member={method}')
        return '\n'.join(lines)

    def test_counts_independent_consent_requests(self):
        result = probe.analyze_pending_portal(fixture()[0], '', self.monitor(3))
        self.assertTrue(result['passed'])
        self.assertEqual(result['portal_calls'],
                         dict(create_session=3, start=3, backend_start=3, connect_to_eis=0))

    def test_requires_sender_and_real_portal_backend(self):
        self.assertFalse(probe.analyze_pending_portal('', '', self.monitor())['passed'])
        self.assertFalse(probe.analyze_pending_portal(fixture()[0], '', '')['passed'])
        no_backend = self.monitor().replace('org.freedesktop.impl.portal.RemoteDesktop', 'wrong')
        self.assertFalse(probe.analyze_pending_portal(fixture()[0], '', no_backend)['passed'])

    def test_pending_consent_must_not_deliver_input(self):
        self.assertFalse(probe.analyze_pending_portal(*fixture(), self.monitor())['passed'])
        monitor = self.monitor() + ('\nmethod call interface=org.freedesktop.portal.RemoteDesktop; '
                                    'member=ConnectToEIS')
        self.assertFalse(probe.analyze_pending_portal(fixture()[0], '', monitor)['passed'])


if __name__ == '__main__':
    unittest.main()
