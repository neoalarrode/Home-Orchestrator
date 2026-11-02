"""Modo "combined" con el contador leido directamente: la red neta sale de la
lectura del Shelly que ya hace el seguimiento (1/s) y no de la entidad de HA
(~15 s). 2-nov: el consumo de la casa sumaba la carga de las baterias porque el
sensor "Consumo Instantaneo" restaba un sensor de la integracion oficial ya
retirada."""
import os, sys, time, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import load_follower as lf  # noqa: E402
import main  # noqa: E402


class MeterNow(unittest.TestCase):
    def _f(self):
        return lf.LoadFollower(lambda: {}, lambda *a: True)

    def test_none_before_any_reading(self):
        self.assertIsNone(self._f().meter_now_w())

    def test_fresh_reading(self):
        f = self._f(); f._last_meter = 1866.0; f._last_meter_ts = time.monotonic()
        self.assertEqual(f.meter_now_w(), 1866.0)

    def test_stale_reading_is_not_used(self):
        f = self._f(); f._last_meter = 1866.0
        f._last_meter_ts = time.monotonic() - lf.METER_LOST_SECONDS - 1
        self.assertIsNone(f.meter_now_w())


class NetGridNow(unittest.TestCase):
    main = main

    def test_prefers_direct_meter(self):
        fake = mock.Mock(); fake.meter_now_w.return_value = 1866.0
        with mock.patch.object(self.main, "_load_follower", fake), \
             mock.patch.object(self.main.ha_client, "get_numeric_state", return_value=999.0) as ha:
            self.assertEqual(self.main._net_grid_now_w({"net_grid_sensor": "sensor.shelly"}), 1866.0)
        ha.assert_not_called()

    def test_falls_back_to_ha_entity(self):
        fake = mock.Mock(); fake.meter_now_w.return_value = None
        with mock.patch.object(self.main, "_load_follower", fake), \
             mock.patch.object(self.main.ha_client, "get_numeric_state", return_value=999.0):
            self.assertEqual(self.main._net_grid_now_w({"net_grid_sensor": "sensor.shelly"}), 999.0)

    def test_combined_total_subtracts_charge(self):
        # 1866 W de red, 700 W cargando, sin sol ni descarga -> 1166 W de casa
        fake = mock.Mock(); fake.meter_now_w.return_value = 1866.0
        cfg = {"load_sensor_mode": "combined", "net_grid_sensor": "sensor.shelly"}
        with mock.patch.object(self.main, "_load_follower", fake):
            self.assertEqual(self.main._live_total_load_w(cfg, 0.0, 700.0, 0.0, True), 1166.0)


if __name__ == "__main__":
    unittest.main()
