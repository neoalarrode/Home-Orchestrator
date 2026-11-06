"""6-nov (decision del usuario): el contador de la casa (Pro 3EM) lo lee el
plugin de Shelly y lo ofrece por dentro (get_handle("meter")) a Energy,
Lighting y Climate."""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from shelly import device_manager as dm  # noqa: E402


class _Resp:
    def __init__(self, data): self.data = data
    def raise_for_status(self): pass
    def json(self): return self.data


class Meter(unittest.TestCase):
    def setUp(self):
        self.m = dm.ShellyDeviceManager()

    def _add(self):
        def fake_get(url, params=None, timeout=None):
            if url.endswith("/shelly"):
                return _Resp({"gen": 2, "model": "SPEM-003CEBEU"})
            if url.endswith("Shelly.GetStatus"):
                return _Resp({"em:0": {}, "emdata:0": {}})
            if url.endswith("EM.GetStatus"):
                return _Resp({"total_act_power": 1234.5, "a_act_power": 0.0, "c_act_power": 1234.5})
            raise AssertionError(url)
        with mock.patch.object(dm.requests, "get", fake_get), \
                mock.patch.object(dm.requests.Session, "get", lambda self, url, params=None, timeout=None: fake_get(url, params, timeout)):
            self.m.add_device("c1", "192.168.1.112")
            h = self.m.meter_handle("c1")
            return h, h.read_now()

    def test_detected_as_meter_and_read(self):
        h, w = self._add()
        self.assertEqual(self.m.get_device("c1")["capability"], "meter")
        self.assertEqual(w, 1234.5)
        self.assertTrue(h.available)
        self.assertEqual(h.host, "192.168.1.112")

    def test_no_light_handle_for_meter(self):
        self._add()
        import shelly_plugin
        p = shelly_plugin.ShellyPlugin.__new__(shelly_plugin.ShellyPlugin)
        p._manager = self.m
        self.assertIsNone(p.get_handle("light", "c1"))
        self.assertIsNotNone(p.get_handle("meter", "c1"))

    def test_unreachable_meter_reads_none(self):
        self._add()
        with mock.patch.object(dm.requests.Session, "get", side_effect=dm.requests.ConnectionError()):
            self.assertIsNone(self.m.meter_handle("c1").read_now())


if __name__ == "__main__":
    unittest.main()
