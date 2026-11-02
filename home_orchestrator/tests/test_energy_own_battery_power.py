"""Una STREAM sola en su grupo mide con `battery_power` (su propia bateria: sol
por DC y enchufes incluidos). 2-nov: la Ultra X, con las placas del patio,
"descargo" 5,0 kWh por la red entre el 96 % y el 8 % de sus 3,84 kWh."""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import main  # noqa: E402


def _bat(i, sn, main_sn):
    return {"id": i, "name": i, "source": "ecoflow", "ecoflow_mode": "bluetooth",
            "ecoflow_ble_address": f"AA:{i}", "ecoflow_sn": sn, "ecoflow_main_sn": main_sn, "capacity_wh": 3840}


def _live(batteries, state):
    cfg = {"ecoflow_user_id": "u", "batteries": batteries}
    with mock.patch.object(main.ecoflow_ble, "get_state", lambda *a, **k: dict(state)):
        return {b["id"]: b for b in main._live_battery_totals(cfg)["battery_live"]}


class OwnBatteryPower(unittest.TestCase):
    def test_solar_charging_while_feeding_the_house(self):
        # 600 W de sol por DC, 400 W a la casa: la bateria GANA 200 W.
        b = _live([_bat("ux", "SN1", "SN1")], {"battery_level_main": 50, "battery_power": 200.0,
                                               "grid_power": 400.0, "ac_power_2": 30.0})["ux"]
        self.assertAlmostEqual(b["net_power_w"], 200.0)
        self.assertEqual(b["outlets_w"], 30.0)       # se sigue mostrando, pero no se resta

    def test_discharge_matches_house_plus_outlets(self):
        b = _live([_bat("ux", "SN1", "SN1")], {"battery_level_main": 50, "battery_power": -1197.7,
                                               "grid_power": 1157.6, "ac_power_2": 36.6})["ux"]
        self.assertAlmostEqual(b["net_power_w"], -1197.7)

    def test_grouped_units_keep_the_per_unit_grid_reading(self):
        # En un grupo `battery_power` es del pack entero: se sigue con grid_power.
        bats = [_bat("m", "SN1", "SN1"), _bat("s", "SN2", "SN1")]
        live = _live(bats, {"battery_level_main": 50, "battery_power": -900.0, "grid_power": 300.0})
        self.assertAlmostEqual(live["s"]["net_power_w"], -300.0)
        self.assertAlmostEqual(live["m"]["net_power_w"], -300.0)


if __name__ == "__main__":
    unittest.main()
