import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/packaging'))
import tray_registration as m


class TrayRegistrationTests(unittest.TestCase):
    def test_default_invocation_never_opens_an_x_connection(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(m.c, 'CDLL') as load:
            with self.assertRaisesRegex(RuntimeError, 'isolated'):
                m.PrivateTray(':0')
            load.assert_not_called()

    def test_lab_marker_alone_does_not_authorize_host_network(self):
        with patch.dict(os.environ, {'PENGUIN_ISOLATED_UI_PROBE': '1'}), \
                patch.object(m.socket, 'if_nameindex', return_value=[(1, 'lo'), (2, 'eth0')]), \
                patch.object(m.c, 'CDLL') as load:
            with self.assertRaisesRegex(RuntimeError, 'isolated'):
                m.PrivateTray(':0')
            load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
