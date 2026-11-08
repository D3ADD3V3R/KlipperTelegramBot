import unittest
from klipper_telegram.camera import CameraError, safe_snapshot_url, select_camera


class CameraTests(unittest.TestCase):
    def test_local_relative_url(self):
        self.assertEqual(safe_snapshot_url('/webcam/?action=snapshot', 'http://127.0.0.1'),
                         'http://127.0.0.1/webcam/?action=snapshot')

    def test_remote_denied_by_default(self):
        with self.assertRaises(CameraError):
            safe_snapshot_url('http://example.com/private', 'http://127.0.0.1')

    def test_redirect_style_denied(self):
        with self.assertRaises(CameraError):
            safe_snapshot_url('//example.com/camera', 'http://127.0.0.1')

    def test_credentials_denied(self):
        with self.assertRaises(CameraError):
            safe_snapshot_url('http://user:password@127.0.0.1/snap', 'http://127.0.0.1')

    def test_explicit_local_host(self):
        self.assertEqual(safe_snapshot_url('http://camera.lan/snap', 'http://127.0.0.1', ['camera.lan']),
                         'http://camera.lan/snap')

    def test_named_camera(self):
        cams = [{'name': 'PrinterCam'}, {'name': 'Rear'}]
        self.assertEqual(select_camera(cams, 'rear')['name'], 'Rear')
        self.assertEqual(select_camera(cams, '', 'PrinterCam')['name'], 'PrinterCam')
        with self.assertRaises(CameraError):
            select_camera(cams, 'nonexistent')
