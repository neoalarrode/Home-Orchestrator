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

    def test_different_value_waits_the_window_and_is_sent(self):
        # 4-nov 01:19 (Salon): apagar y a los 1,6 s volver a encender. Antes se
        # rechazaba y la lampara tardaba 5 s; ahora espera lo que falta y manda.
        clock = [1000.0]
        slept = []
        orig_time, orig_sleep = govee_cloud.time.time, govee_cloud.time.sleep
        govee_cloud.time.time = lambda: clock[0]
        govee_cloud.time.sleep = lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s))
        try:
            self.assertTrue(govee_cloud.control("k", "AA", "H6008", "turn", "off"))
            clock[0] += 1.6
            self.assertTrue(govee_cloud.control("k", "AA", "H6008", "turn", "on"))
        finally:
            govee_cloud.time.time, govee_cloud.time.sleep = orig_time, orig_sleep
        self.assertEqual([c["value"] for c in self.sent], ["off", "on"])
        self.assertAlmostEqual(slept[0], govee_cloud.MIN_SECONDS_BETWEEN_CONTROL - 1.6, places=3)

    def test_failed_send_is_retried_after_the_window(self):
        # Un envio que fallo no cuenta como mandado: repetirlo espera la ventana
        # y lo vuelve a intentar (antes se descartaba).
        self.code = 500
        clock = [1000.0]
        orig_time, orig_sleep = govee_cloud.time.time, govee_cloud.time.sleep
        govee_cloud.time.time = lambda: clock[0]
        govee_cloud.time.sleep = lambda s: clock.__setitem__(0, clock[0] + s)
        try:
            with self.assertLogs("govee_cloud", "WARNING"):
                self.assertFalse(govee_cloud.control("k", "AA", "H6008", "turn", "on"))
                self.assertFalse(govee_cloud.control("k", "AA", "H6008", "turn", "on"))
        finally:
            govee_cloud.time.time, govee_cloud.time.sleep = orig_time, orig_sleep
        self.assertEqual(len(self.sent), 2)
        self.assertGreaterEqual(clock[0] - 1000.0, govee_cloud.MIN_SECONDS_BETWEEN_CONTROL)


if __name__ == "__main__":
    unittest.main()
