"""Energy planifica con la calefaccion que PREVE Climate (4-nov): previsión =
historico sin climatizacion + previsión de Climate, sin contarla dos veces."""
import os, sys, tempfile, unittest
from datetime import datetime, timedelta
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="ho_climsplit_")
os.environ["CLIMATE_SPLIT_PATH"] = os.path.join(_TMP, "climate_split.json")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate_link  # noqa: E402
import main  # noqa: E402


class Combine(unittest.TestCase):
    def test_sums_zones_aligned_to_the_current_hour(self):
        now = datetime(2026, 11, 4, 3, 20).astimezone()
        start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
        a = {"start": start.isoformat(), "w": [100.0, 200.0, 300.0]}
        b = {"start": start.isoformat(), "w": [10.0, 20.0, 30.0]}
        self.assertEqual(climate_link.combine_heat_forecasts([a, b], now), [220.0, 330.0])

    def test_any_zone_without_forecast_means_none(self):
        now = datetime(2026, 11, 4, 3, 0).astimezone()
        ok = {"start": now.isoformat(), "w": [1.0]}
        self.assertIsNone(climate_link.combine_heat_forecasts([ok, {"start": now.isoformat(), "w": None}], now))
        self.assertIsNone(climate_link.combine_heat_forecasts([ok, None], now))
        self.assertIsNone(climate_link.combine_heat_forecasts([], now))


class Apply(unittest.TestCase):
    def setUp(self):
        self.cfg = {"climate_orchestrator_zones": ["climate.a"]}
        self.path = os.path.join(_TMP, f"split_{self._testMethodName}.json")
        self.p = [mock.patch.object(main, "CLIMATE_SPLIT_PATH", self.path),
                  mock.patch.object(main, "_publish_sensor_throttled", lambda *a, **k: None),
                  mock.patch.object(main.ha_client, "hourly_average_forecast", lambda *a, **k: [400.0, 400.0, 400.0])]
        for x in self.p: x.start()

    def tearDown(self):
        for x in self.p: x.stop()

    def test_waits_for_enough_climate_history(self):
        now = datetime(2026, 11, 4, 3, 0)
        lf, base = main._apply_climate_forecast(self.cfg, [1000.0] * 3, {"total_w": 0, "heat_forecast": [2000.0] * 3}, 3, 10, now)
        self.assertEqual(lf, [1000.0] * 3)              # primer dia: como siempre

    def test_replaces_past_heating_with_the_forecast(self):
        start = datetime(2026, 11, 1, 3, 0)
        main.json_store.save(self.path, {"since": start.isoformat()})
        now = start + timedelta(days=3, hours=1)
        lf, base = main._apply_climate_forecast(self.cfg, [1000.0] * 3, {"total_w": 0, "heat_forecast": [2000.0, 0.0]}, 3, 10, now)
        self.assertEqual(base, [600.0] * 3)             # 1000 de media - 400 de calefaccion de otros dias
        self.assertEqual(lf, [2600.0, 600.0, 1000.0])   # + lo previsto; sin previsión, la media de siempre

    def test_without_climate_nothing_changes(self):
        lf, base = main._apply_climate_forecast({}, [500.0], {"total_w": 0}, 1, 10, datetime(2026, 11, 4))
        self.assertEqual((lf, base), ([500.0], [500.0]))


if __name__ == "__main__":
    unittest.main()
