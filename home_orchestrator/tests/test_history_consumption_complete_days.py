"""La comparacion "hoy frente a los ultimos dias" solo usa dias con el tramo
completo: un dia con medio historico (addon parado) bajaba la media."""
import os, sys, tempfile, unittest
from datetime import datetime

_TMP = tempfile.mkdtemp(prefix="ho_hist_")
os.environ["HISTORY_PATH"] = os.path.join(_TMP, "history.json")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import history_store  # noqa: E402


class CompleteDays(unittest.TestCase):
    def _run(self, data, now):
        orig = history_store._load
        history_store._load = lambda: data
        try:
            return history_store.get_recent_days_consumption(now, days=7)
        finally:
            history_store._load = orig

    def test_partial_day_is_ignored(self):
        data = {}
        for h in range(10):
            data[f"2026-11-01T{h:02d}"] = {"load_w": 500}
            data[f"2026-11-02T{h:02d}"] = {"load_w": 500}
        for h in range(3):                       # 31-oct: addon parado casi toda la manana
            data[f"2026-10-31T{h:02d}"] = {"load_w": 500}
        r = self._run(data, datetime(2026, 11, 2, 10, 30))
        self.assertEqual(r["days_compared"], 1)
        self.assertEqual(r["delta_pct"], 0.0)

    def test_one_missing_hour_tolerated(self):
        data = {f"2026-11-01T{h:02d}": {"load_w": 400} for h in range(10) if h != 2}
        data.update({f"2026-11-02T{h:02d}": {"load_w": 400} for h in range(10)})
        r = self._run(data, datetime(2026, 11, 2, 10, 0))
        self.assertEqual(r["days_compared"], 1)


if __name__ == "__main__":
    unittest.main()
