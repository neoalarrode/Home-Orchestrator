"""La carga de cada unidad STREAM descuenta lo que sale por sus enchufes
Schuko: `grid_power` es lo que la unidad toma de red, enchufes incluidos. Caso
real 1-nov: EF-60999 "cargando" a 248 W con 113 W entrando de verdad y 125 W
saliendo por su Schuko 2. Por BLE (`ac_power_1/2`) y por Cloud (`powGetSchuko1/2`)."""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import main  # noqa: E402


def _cfg(mode):
    return {"ecoflow_user_id": "u", "ecoflow_access_key": "a", "ecoflow_secret_key": "s", "batteries": [
        {"id": "b1", "name": "EF-60999", "source": "ecoflow", "ecoflow_mode": mode,
         "ecoflow_ble_address": "CC:BA", "ecoflow_sn": "SN1", "ecoflow_main_sn": "SN1",
         "capacity_wh": 2000},
    ]}


class _Cloud:
    def __init__(self, state): self.state = state
    def get_live_state(self, sn, required_fields=None): return dict(self.state)


class Outlets(unittest.TestCase):
    def run_live(self, cfg, ble=None, cloud=None):
        with mock.patch.object(main.ecoflow_ble, "get_state", lambda *a, **k: ble), \
             mock.patch.object(main.ecoflow_cloud, "get_client", lambda *a, **k: _Cloud(cloud or {})):
            return main._live_battery_totals(cfg)["battery_live"][0]

    def test_ble_outlets_are_subtracted(self):
        b = self.run_live(_cfg("bluetooth"), ble={"battery_level_main": 46, "grid_power": -248.0,
                                                   "ac_power_1": 0.0, "ac_power_2": 125.0})
        self.assertAlmostEqual(b["net_power_w"], 123.0)
        self.assertEqual(b["outlets_w"], 125.0)

    def test_cloud_outlets_are_subtracted(self):
        b = self.run_live(_cfg("cloud"), cloud={"bmsBattSoc": 46, "gridConnectionPower": -248.0,
                                                "powGetSchuko1": 0.0, "powGetSchuko2": 125.0})
        self.assertAlmostEqual(b["net_power_w"], 123.0)

    def test_hybrid_old_bridge_completes_outlets_from_cloud(self):
        b = self.run_live(_cfg("hybrid"), ble={"battery_level_main": 46, "grid_power": -248.0},
                          cloud={"powGetSchuko2": 125.0})
        self.assertAlmostEqual(b["net_power_w"], 123.0)

    def test_discharging_battery_also_feeds_the_outlets(self):
        b = self.run_live(_cfg("bluetooth"), ble={"battery_level_main": 46, "grid_power": 300.0,
                                                   "ac_power_1": 50.0})
        self.assertAlmostEqual(b["net_power_w"], -350.0)     # 300 a la casa + 50 por el enchufe
        self.assertAlmostEqual(b["power_w"], 350.0)

    def test_without_outlet_data_behaves_as_before(self):
        b = self.run_live(_cfg("bluetooth"), ble={"battery_level_main": 46, "grid_power": -248.0})
        self.assertAlmostEqual(b["net_power_w"], 248.0)
        self.assertIsNone(b["outlets_w"])


if __name__ == "__main__":
    unittest.main()
