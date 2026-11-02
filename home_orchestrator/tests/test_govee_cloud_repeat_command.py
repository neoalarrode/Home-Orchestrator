"""Repetir el mismo valor de un comando cloud dentro de la ventana de 3 s no es
un fallo: ya esta mandado (2-nov, Escalera: Lighting volcaba la traza y
reenviaba el mismo `turn`)."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import govee_cloud  # noqa: E402


class _R:
    def __init__(self, code=200): self.code = code
    def raise_for_status(self): pass
    def json(self): return {"code": self.code, "message": "x"}


class RepeatCommand(unittest.TestCase):
    def setUp(self):
        self._orig = govee_cloud.requests.put
        self.sent = []
        self.code = 200
        govee_cloud.requests.put = lambda url, **k: (self.sent.append(k["json"]["cmd"]), _R(self.code))[1]
        govee_cloud._last_control_at.clear()
        govee_cloud._last_control_ok.clear()

    def tearDown(self):
        govee_cloud.requests.put = self._orig

    def test_same_value_counts_as_sent(self):
        self.assertTrue(govee_cloud.control("k", "AA", "H6008", "turn", "on"))
        self.assertTrue(govee_cloud.control("k", "AA", "H6008", "turn", "on"))
        self.assertEqual(len(self.sent), 1)

    def test_different_value_is_still_deferred(self):
        self.assertTrue(govee_cloud.control("k", "AA", "H6008", "turn", "on"))
        with self.assertLogs("govee_cloud", "WARNING"):
            self.assertFalse(govee_cloud.control("k", "AA", "H6008", "turn", "off"))
        self.assertEqual(len(self.sent), 1)

    def test_failed_send_is_not_remembered(self):
        self.code = 500
        with self.assertLogs("govee_cloud", "WARNING"):
            self.assertFalse(govee_cloud.control("k", "AA", "H6008", "turn", "on"))
            self.assertFalse(govee_cloud.control("k", "AA", "H6008", "turn", "on"))
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
