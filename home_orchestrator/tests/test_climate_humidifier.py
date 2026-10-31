"""Humidificador gobernado por el sensor de humedad de la ZONA con histeresis
(antes: siempre encendido fiandose de su higrostato; 31-oct, encendido al 67%)."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402


class _Stub:
    def __init__(self, target=45.0):
        self.target_humidity = target
        self._humidifying = False
        self.hum = None
        self.wanted = zr.ZoneRunner._humidify_wanted.__get__(self)
    def _read_humidity_now(self): return self.hum


class Humidifier(unittest.TestCase):
    def test_off_when_humid(self):
        s = _Stub(); s.hum = 67.0
        self.assertFalse(s.wanted())

    def test_hysteresis(self):
        s = _Stub()
        s.hum = 43.0; self.assertFalse(s.wanted())   # entre 42 y 45: no arranca
        s.hum = 41.5; self.assertTrue(s.wanted())    # < 42: arranca
        s.hum = 44.0; self.assertTrue(s.wanted())    # sigue hasta el objetivo
        s.hum = 45.0; self.assertFalse(s.wanted())   # llega: para

    def test_without_sensor_keeps_old_behaviour(self):
        self.assertTrue(_Stub().wanted())


if __name__ == "__main__":
    unittest.main()
