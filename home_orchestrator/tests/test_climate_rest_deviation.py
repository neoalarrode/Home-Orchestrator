"""Desviacion de sonda aprendida EN REPOSO (dormitorio 1-nov: AC en lo alto de la
pared leyendo ~3.8 C sobre la sala; la banda de 2 C la rechazaba, compensacion 0,
consigna 21 con su sonda en 24 -> no calentaba)."""
import os, sys, unittest
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402

T0 = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)


class _Clock:
    def __init__(self): self.now = T0; self._o = zr._utcnow
    def __enter__(self): zr._utcnow = lambda: self.now; return self
    def __exit__(self, *a): zr._utcnow = self._o


class _Stub:
    def __init__(self):
        self.zone = {"name": "Dormitorio"}
        self._delegate_deviations, self._park_since = {}, {}
        self._deviation_at_rest = set(); self._hold_run = {}; self._hold_trim = {}; self._rest_dev_at = {}
        self.learn = zr.ZoneRunner._learn_rest_deviation.__get__(self)
        self.comp = zr.ZoneRunner._compensate_delegate_target.__get__(self)


def _state(dev_temp):
    return {"state": "heat", "attributes": {"current_temperature": dev_temp, "min_temp": 16.0, "max_temp": 30.0}}


class RestDeviation(unittest.TestCase):
    def test_learns_real_offset_after_settling_and_heats(self):
        with _Clock() as c:
            s = _Stub()
            s.learn("ac", {"current_temperature": 24.0}, 20.2)
            self.assertNotIn("ac", s._deviation_at_rest)            # aun no asentado
            c.now += timedelta(minutes=31)
            s.learn("ac", {"current_temperature": 24.0}, 20.2)
            self.assertAlmostEqual(s._delegate_deviations["ac"], 3.8, places=2)
            # Calentar hacia 21: consigna 24.8 (por encima de su sonda de 24 -> calienta)
            self.assertAlmostEqual(s.comp("ac", _state(24.0), 21.0, 20.2), 24.8, places=2)

    def test_frozen_while_heating_no_runaway(self):
        with _Clock() as c:
            s = _Stub()
            s.learn("ac", {"current_temperature": 24.0}, 20.2)
            c.now += timedelta(minutes=31)
            s.learn("ac", {"current_temperature": 24.0}, 20.2)
            sent = [s.comp("ac", _state(24.0 + k), 21.0, 20.3) for k in range(8)]  # su sonda trepa
            self.assertTrue(all(abs(x - 24.8) < 1e-6 for x in sent), sent)

    def test_without_rest_learning_old_band_fallback(self):
        s = _Stub()
        self.assertAlmostEqual(s.comp("ac", _state(21.0), 21.0, 20.4), 21.6, places=2)   # 0.6 dentro de banda

    def test_absurd_offset_ignored(self):
        with _Clock() as c:
            s = _Stub()
            s.learn("ac", {"current_temperature": 35.0}, 20.0)
            c.now += timedelta(minutes=31)
            s.learn("ac", {"current_temperature": 35.0}, 20.0)
            self.assertNotIn("ac", s._deviation_at_rest)


if __name__ == "__main__":
    unittest.main()
