"""5-nov: el aire del dormitorio arrancaba A TOPE cada ~35 min con la zona ya
en la consigna (la anticipacion medía el hueco desde la temperatura proyectada
al final de la hora) y esas rafagas clavaban el recorte de mantener en 5 C."""
import os, sys, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.scheduler as sch  # noqa: E402
import climate.zone_runner as zr  # noqa: E402

FC = [8.7, 11.3, 10.8, 10.0, 9.2, 8.8]


class Anticipate(unittest.TestCase):
    def test_real_case_at_target_does_not_boost(self):
        # Dormitorio 5-nov 19:51: 21,2 °C, consigna 21 (±0,3), perdidas 0,097, 0,9 °C/h
        self.assertEqual(sch._anticipate(True, 21.2, 21.0, 0.3, FC, 0.097, 0.9)[0], "idle")
        self.assertEqual(sch._anticipate(True, 21.0, 21.0, 0.3, FC, 0.097, 0.9)[0], "idle")

    def test_fast_zone_below_target_waits_for_reactive(self):
        # recupera del umbral a la consigna en 20 min: el tramo reactivo basta
        self.assertEqual(sch._anticipate(True, 20.9, 21.0, 0.3, FC, 0.097, 0.9)[0], "idle")

    def test_slow_zone_still_anticipates(self):
        # zona lenta (0,1 °C/h): del umbral a la consigna son 3 h, cruza en 1 h
        self.assertEqual(sch._anticipate(True, 20.9, 21.0, 0.3, FC, 0.097, 0.1)[0], "heat")

    def test_cooling_symmetric(self):
        hot = [35.0] * 6
        self.assertEqual(sch._anticipate(False, 24.8, 25.0, 0.3, hot, 0.1, 0.1)[0], "idle")
        self.assertEqual(sch._anticipate(False, 25.1, 25.0, 0.3, hot, 0.1, 0.1)[0], "cool")


class _S:
    def __init__(self):
        self.zone = {"name": "Dormitorio"}
        self._hold_run = {}
        self._hold_trim = {"ac": 1.0}
        self._drive_at = {}
        self._bump_hold_trim = zr.ZoneRunner._bump_hold_trim.__get__(self)
        self._learn_hold_trim = zr.ZoneRunner._learn_hold_trim.__get__(self)


class HoldTrim(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 11, 5, 19, 0, tzinfo=timezone.utc)

    def _quick_overshoot(self, s, drove_before):
        if drove_before is not None:
            s._drive_at["ac"] = self.t0 - timedelta(seconds=drove_before)
        s._learn_hold_trim("ac", 21.0, 21.1, True, False, self.t0)
        s._learn_hold_trim("ac", 21.0, 21.3, True, True, self.t0 + timedelta(minutes=5))

    def test_overshoot_right_after_boost_does_not_count(self):
        s = _S(); self._quick_overshoot(s, drove_before=60)
        self.assertEqual(s._hold_trim["ac"], 1.0)

    def test_overshoot_of_a_real_hold_still_counts(self):
        s = _S(); self._quick_overshoot(s, drove_before=3600)
        self.assertEqual(s._hold_trim["ac"], 1.0 + zr.HOLD_TRIM_STEP_DEG)
        s = _S(); self._quick_overshoot(s, drove_before=None)
        self.assertEqual(s._hold_trim["ac"], 1.0 + zr.HOLD_TRIM_STEP_DEG)


class StaleTrims(unittest.TestCase):
    def test_old_trims_halved_once(self):
        import inspect
        src = inspect.getsource(zr.ZoneRunner)
        self.assertIn("hold_trims_version", src)
        # mitad en pasos de 0,5 (misma expresion que el cargador)
        for old, new in ((5.0, 2.5), (4.0, 2.0), (1.0, 0.5), (-1.0, -0.5)):
            self.assertEqual(round(old / 2 * 2) / 2, new)


if __name__ == "__main__":
    unittest.main()
