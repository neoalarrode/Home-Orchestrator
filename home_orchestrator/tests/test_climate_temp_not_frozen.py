"""La temperatura de zona sigue al sensor aunque HA no actualice `last_updated`.
Caso real 1-nov: con subscribe_entities, al cambiar el valor HA manda solo `lc`;
`last_updated` se quedaba con la fecha del arranque y la EMA (fechada con ella)
congelaba la lectura: cuarto de juegos 40 min en 20.79 con el sensor en 20.15."""
import os, sys, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402
from climate import ema as ema_module  # noqa: E402


class _Stub:
    def __init__(self):
        self.zone = {zr.CONF_CURRENT_TEMP_SENSOR: "sensor.t", "name": "Cuarto"}
        self.zone_id = "z"
        self._temp_ema = ema_module.Ema(zr.TEMP_EMA_HALFLIFE_SECONDS)
        self._sensor_stale = False
        self.state = None
        self.read = zr.ZoneRunner._read_current_temp.__get__(self)
    def _get_state(self, e): return self.state


class TempFollowsSensor(unittest.TestCase):
    def setUp(self):
        self._orig = zr._utcnow
        self.t = [datetime(2026, 11, 1, 17, 17, tzinfo=timezone.utc)]
        zr._utcnow = lambda: self.t[0]

    def tearDown(self):
        zr._utcnow = self._orig

    def test_value_change_with_stale_last_updated(self):
        s = _Stub()
        seed = "2026-11-01T16:03:44+00:00"           # ISO de get_states al arrancar
        s.state = {"state": "20.79", "last_changed": seed, "last_updated": seed}
        self.assertEqual(s.read(), 20.79)
        # cambia el valor: llega solo `lc` (epoch); `last_updated` sigue con el ISO viejo
        s.state = {"state": "20.15", "last_changed": self.t[0].timestamp() + 60, "last_updated": seed}
        for _ in range(15):                            # 15 ciclos de 1 min
            self.t[0] += timedelta(minutes=1)
            value = s.read()
        self.assertAlmostEqual(value, 20.15, delta=0.02)

    def test_single_report_is_fully_reached(self):
        s = _Stub()
        s.state = {"state": "21.0"}
        s.read()
        s.state = {"state": "22.0"}
        for _ in range(10):
            self.t[0] += timedelta(minutes=1)
            value = s.read()
        self.assertGreater(value, 21.95)               # no se queda a medio camino

    def test_smooths_a_spike(self):
        s = _Stub()
        s.state = {"state": "21.0"}
        s.read()
        self.t[0] += timedelta(seconds=30)
        s.state = {"state": "24.0"}                    # pico puntual
        self.assertLess(s.read(), 22.0)


if __name__ == "__main__":
    unittest.main()
