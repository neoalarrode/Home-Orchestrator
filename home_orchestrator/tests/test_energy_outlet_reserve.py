"""6-nov (decision del usuario): no se reserva nada para los enchufes de una
bateria -- toda su energia esta disponible; lo que gastaran hasta el valle
(`outlet_drain_wh`) solo sirve para el orden de entrada a descargar."""
import os, sys, unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import main  # noqa: E402

PLAN = [{"dt": f"2026-11-05T{h:02d}:00:00", "tier": t} for h, t in ((18, "punta"), (19, "punta"), (20, "punta"), (21, "punta"), (22, "llano"), (23, "llano"))] + \
       [{"dt": "2026-11-06T00:00:00", "tier": "valle"}]


class HoursUntilValle(unittest.TestCase):
    def test_counts_to_the_next_valle(self):
        with mock.patch.dict(main._last_status, {"plan": PLAN}):
            self.assertAlmostEqual(main._hours_until_valle(datetime(2026, 11, 5, 18, 30)), 5.5)

    def test_zero_inside_valle_or_without_plan(self):
        with mock.patch.dict(main._last_status, {"plan": PLAN}):
            self.assertEqual(main._hours_until_valle(datetime(2026, 11, 6, 0, 20)), 0.0)
        with mock.patch.dict(main._last_status, {"plan": []}):
            self.assertEqual(main._hours_until_valle(datetime(2026, 11, 5, 18, 0)), 0.0)


class OutletDrain(unittest.TestCase):
    def test_no_reserve_but_drain_reported(self):
        cfg = {"ecoflow_user_id": "u", "general": {"load_follow_mode": "on"}, "batteries": [
            {"id": "ef", "name": "EF", "source": "ecoflow", "ecoflow_ble_address": "A1", "ecoflow_sn": "S1",
             "ecoflow_main_sn": "S1", "capacity_wh": 1920, "min_soc_pct": 3},
            {"id": "mp", "name": "MP", "source": "ecoflow", "ecoflow_ble_address": "A2", "ecoflow_sn": "S2",
             "ecoflow_main_sn": "S2", "capacity_wh": 1920, "min_soc_pct": 3}]}
        states = {"A1": {"battery_level_main": 33, "energy_monitoring_mode": "semi_automated", "ac_power_2": 100.0},
                  "A2": {"battery_level_main": 33, "energy_monitoring_mode": "semi_automated"}}
        with mock.patch.object(main.config_store, "load_config", lambda: cfg), \
             mock.patch.object(main.ecoflow_ble, "get_state", lambda addr, uid, **k: states[addr]), \
             mock.patch.object(main, "_hours_until_valle", lambda now=None: 3.0), \
             mock.patch.object(main, "_load_follow_test_ids", lambda c: set()):
            ctx = main._load_follow_context()
        av = {f["id"]: f["available_wh"] for f in ctx["followers"]}
        drain = {f["id"]: f["outlet_drain_wh"] for f in ctx["followers"]}
        self.assertAlmostEqual(av["mp"], 0.30 * 1920, delta=1)
        self.assertAlmostEqual(av["ef"], 0.30 * 1920, delta=1)          # sin reserva
        self.assertAlmostEqual(drain["ef"], 300, delta=1)               # 100 W x 3 h, solo para el orden
        self.assertEqual(drain["mp"], 0.0)


if __name__ == "__main__":
    unittest.main()
