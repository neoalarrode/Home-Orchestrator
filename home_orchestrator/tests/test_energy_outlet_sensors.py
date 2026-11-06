"""5-nov: Energy publica un sensor de potencia y otro de energia por cada
enchufe de bateria (lo enchufado ahi no lo ve el Shelly mientras tira de bateria)."""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import main  # noqa: E402


class OutletSensors(unittest.TestCase):
    def setUp(self):
        self.sent = {}
        self.store = {}
        main._outlet_last_ts.clear()

        def acc(key, wh):
            e = self.store.setdefault(key, {"since": None, "wh": 0.0})
            if wh > 0:
                e["since"] = e["since"] or "2026-11-05T22:30:00"
                e["wh"] += wh
            return dict(e)
        self.p = [mock.patch.object(main, "_publish_sensor_throttled", lambda e, s, a, **k: self.sent.__setitem__(e, (s, a))),
                  mock.patch.object(main.outlet_energy_store, "accumulate", acc),
                  mock.patch.object(main.monotonic_sensor, "publishable", lambda e, t, **k: t)]
        for x in self.p: x.start()

    def tearDown(self):
        for x in self.p: x.stop()

    def test_each_outlet_gets_power_and_energy(self):
        b = [{"id": "EF-60999", "name": "Stream AC Pro", "outlets_each_w": {1: 125.0, 2: 0.0}},
             {"id": "ultra", "name": "Ultra X", "outlets_each_w": {}}]
        main._publish_outlet_sensors(b, now_ts=1000.0)
        self.assertEqual(self.sent["sensor.battery_orchestrator_outlet_ef_60999_1"][0], 125)
        self.assertEqual(self.sent["sensor.battery_orchestrator_outlet_ef_60999_2"][0], 0)
        self.assertNotIn("sensor.battery_orchestrator_outlet_ef_60999_1_energy", self.sent)   # aun sin energia
        main._publish_outlet_sensors(b, now_ts=1060.0)                                         # 1 min a 125 W
        val, attrs = self.sent["sensor.battery_orchestrator_outlet_ef_60999_1_energy"]
        self.assertAlmostEqual(val, 125 / 60 / 1000, delta=0.0006)   # kWh con 3 decimales
        self.assertEqual(attrs["state_class"], "total_increasing")
        self.assertFalse(any("ultra" in k for k in self.sent))

    def test_gap_is_capped(self):
        b = [{"id": "x", "name": "X", "outlets_each_w": {1: 1000.0}}]
        main._publish_outlet_sensors(b, now_ts=0.0)
        main._publish_outlet_sensors(b, now_ts=3600.0)                                         # hueco de 1 h
        self.assertAlmostEqual(self.store["x_1"]["wh"], 1000 * main.SOLAR_ENERGY_MAX_GAP_SECONDS / 3600)

    def test_each_w_parses_fields(self):
        self.assertEqual(main._ecoflow_outlets_each_w({"ac_power_1": -12.5, "ac_power_2": "x"}, main._ECOFLOW_OUTLET_FIELDS_BLE), {1: 12.5})


if __name__ == "__main__":
    unittest.main()
