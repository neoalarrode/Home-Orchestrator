"""La hora actual se planifica con el consumo medido ahora, no con la media
historica (2-nov, 12:00 punta: previsto 663 W, real 1,5-2 kW, 565 W de sol;
el plan oscilaba entre descarga / sin accion / carga solar)."""
import os, sys, unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import cycle_planner  # noqa: E402
import scheduler  # noqa: E402

NOW = datetime(2026, 11, 2, 12, 5)


def _now_hour(pv_now, load):
    n = len(load)
    plan, _ = scheduler.build_plan(
        now=NOW, pv_forecast_w=[pv_now] + [0.0] * (n - 1), load_forecast_w=load, current_soc_wh=7000,
        total_capacity_wh=9600, max_charge_w=4800, max_discharge_w=4800, min_soc_wh=1000,
        prices_tiers=[(0.173, "punta")] * n, allow_grid_charging=True)
    return plan[0]


class LiveLoadNow(unittest.TestCase):
    FORECAST = [663.0, 700.0, 650.0]

    def test_historical_forecast_flips_with_small_pv_changes(self):
        # Lo que pasaba: con la media historica, unos vatios de sol de mas
        # cambian la decision entera.
        self.assertGreater(_now_hour(565, self.FORECAST).discharge_w, 0)
        self.assertEqual(_now_hour(700, self.FORECAST).discharge_w, 0)

    def test_live_load_gives_stable_discharge(self):
        load = cycle_planner.load_with_live_now(self.FORECAST, 1500.0)
        self.assertEqual(load, [1500.0, 700.0, 650.0])
        for pv in (540, 565, 600, 700):
            hp = _now_hour(pv, load)
            self.assertEqual(hp.reason, "descarga para cubrir consumo en punta")
            self.assertAlmostEqual(hp.discharge_w, 1500 - pv)

    def test_without_live_reading_keeps_forecast(self):
        self.assertIs(cycle_planner.load_with_live_now(self.FORECAST, None), self.FORECAST)
        self.assertEqual(cycle_planner.load_with_live_now([], 900.0), [])

    def test_live_reading_never_negative(self):
        self.assertEqual(cycle_planner.load_with_live_now([300.0], -20.0), [0.0])


if __name__ == "__main__":
    unittest.main()
