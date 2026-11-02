"""Integracion de energia de red sin contadores a traves del adelanto de hora
(29-mar-2026 en Madrid: 02:00 -> 03:00): no se cuenta la hora que no existio."""
import os, sys, tempfile, time, unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import grid_energy_store as g  # noqa: E402


@unittest.skipUnless(hasattr(time, "tzset"), "tzset no disponible")
class DstSpringForward(unittest.TestCase):
    def setUp(self):
        self.old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/Madrid"; time.tzset()
        fd, self.path = tempfile.mkstemp(suffix=".json"); os.close(fd); os.unlink(self.path)
        self.p = mock.patch.object(g, "STORE_PATH", self.path); self.p.start()

    def tearDown(self):
        self.p.stop()
        if self.old_tz is None: os.environ.pop("TZ", None)
        else: os.environ["TZ"] = self.old_tz
        time.tzset()
        if os.path.exists(self.path): os.unlink(self.path)

    def test_spring_forward_integrates_seconds_not_an_hour(self):
        g.accumulate(datetime(2026, 3, 29, 1, 59, 58), 1000.0, 0.0)
        out = g.accumulate(datetime(2026, 3, 29, 3, 0, 1), 1000.0, 0.0)
        self.assertLess(out["imported_kwh"], 0.01)        # 3 s a 1 kW, no 1 h (1 kWh)


if __name__ == "__main__":
    unittest.main()
