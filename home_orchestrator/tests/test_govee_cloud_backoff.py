"""Lectura cloud de Govee que falla: no se repite en cada pasada del sondeo y se
avisa una sola vez (2-nov: caida de DNS de 70 min, ~15 lecturas/min y 14.000
lineas de traza en el log)."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import govee.device_manager as dm  # noqa: E402
import govee_cloud  # noqa: E402
import requests  # noqa: E402


class CloudBackoff(unittest.TestCase):
    def setUp(self):
        self.calls = 0
        self.fail = True
        self._orig_get = dm.govee_cloud.get_state
        self._orig_time = dm.time.time
        self.now = 1000.0
        dm.time.time = lambda: self.now

        def fake(key, mac, sku):
            self.calls += 1
            return None if self.fail else {"powerState": "on", "brightness": 40}
        dm.govee_cloud.get_state = fake
        self.m = dm.GoveeDeviceManager()
        self.m.set_cloud_identity("b", "AA:BB", "H6008")
        self.m.set_cloud_api_key("k")

    def tearDown(self):
        dm.govee_cloud.get_state = self._orig_get
        dm.time.time = self._orig_time

    def test_failure_waits_before_retrying(self):
        self.assertIsNone(self.m._cloud_status("b"))
        for dt in (5, 20, 40):
            self.now = 1000 + dt
            self.m._cloud_status("b", fresh=True)
        self.assertEqual(self.calls, 1)
        self.now = 1000 + dm.CLOUD_FAILURE_BACKOFF_SECONDS + 1
        self.fail = False
        self.assertEqual(self.m._cloud_status("b"), {"onOff": 1, "brightness": 40})
        self.assertEqual(self.calls, 2)


class LogOnce(unittest.TestCase):
    def setUp(self):
        self._orig = govee_cloud.requests.get
        govee_cloud._read_failing.clear()

    def tearDown(self):
        govee_cloud.requests.get = self._orig

    def test_warns_once_then_info_on_recovery(self):
        def boom(*a, **k):
            raise requests.ConnectionError("Failed to resolve")
        govee_cloud.requests.get = boom
        with self.assertLogs("govee_cloud", "WARNING") as cm:
            govee_cloud.get_state("k", "AA:BB", "H6008")
            govee_cloud.get_state("k", "AA:BB", "H6008")
        self.assertEqual(len(cm.records), 1)
        self.assertNotIn("Traceback", cm.output[0])

        class R:
            def raise_for_status(self): pass
            def json(self): return {"data": {"properties": [{"powerState": "on"}]}}
        govee_cloud.requests.get = lambda *a, **k: R()
        with self.assertLogs("govee_cloud", "INFO") as cm:
            self.assertEqual(govee_cloud.get_state("k", "AA:BB", "H6008"), {"powerState": "on"})
        self.assertIn("vuelve a responder", cm.output[0])


if __name__ == "__main__":
    unittest.main()
