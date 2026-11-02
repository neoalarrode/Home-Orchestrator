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


class LiveLoadSmoothing(unittest.TestCase):
    def test_lagging_battery_oscillation_averages_out(self):
        # 2-nov 13:07: lecturas alternas 450 / 1000 W cada ~7 s con la casa en ~700.
        sm = cycle_planner.LiveLoadSmoother()
        out = None
        for i in range(40):
            out = sm.update(i * 7.0, 450.0 if i % 2 else 1000.0)
        self.assertAlmostEqual(out, 725, delta=40)
        for pv in (600, 650):
            hp = _now_hour(pv, cycle_planner.load_with_live_now([663.0, 700.0], out))
            self.assertEqual(hp.reason, "descarga para cubrir consumo en punta")

    def test_real_step_shows_up_quickly(self):
        sm = cycle_planner.LiveLoadSmoother()
        for t in range(0, 120, 5):
            sm.update(t, 500.0)
        val = None
        for t in range(120, 160, 5):        # salta el radiador: 2 kW
            val = sm.update(t, 2000.0)
        self.assertGreater(val, 900)        # 40 s despues ya supera al sol (600 W)

    def test_old_samples_expire_and_none_keeps_recent(self):
        sm = cycle_planner.LiveLoadSmoother(window_seconds=60)
        sm.update(0, 300.0)
        self.assertEqual(sm.update(10, None), 300.0)
        self.assertIsNone(sm.update(100, None))


if __name__ == "__main__":
    unittest.main()
