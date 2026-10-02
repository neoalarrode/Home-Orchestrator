"""Pruebas del extractor de vapor con disparo RELATIVO a la linea base
(stdlib unittest): python -m unittest discover -s home_orchestrator/tests

Bug real que motiva esto (bano de arriba, confirmado en produccion): con un
umbral ABSOLUTO (55%) y la humedad de reposo del bano rondando ya el 50-56%,
el extractor no se apagaba casi nunca -- la humedad practicamente nunca bajaba
del punto de corte. La nueva logica dispara por cuanto SUBE la humedad sobre
su propio reposo (una linea base rodante), no por un numero fijo.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.const as const  # noqa: E402
import climate.zone_runner as zr  # noqa: E402


def _stub(active=False, baseline=None, baseline_ts=None):
    """Objeto minimo con los metodos reales del extractor enganchados, sin
    arrancar un ZoneRunner entero (que necesita ws/mqtt/HA)."""
    class _S:
        pass
    s = _S()
    s._extractor_baseline = baseline
    s._extractor_baseline_ts = baseline_ts
    s._extractor_active = active
    s.current_humidity = None
    s.zone = {
        "extractor_switches": ["switch.extractor"],
        "extractor_rise_on": const.DEFAULT_EXTRACTOR_RISE_ON,
        "extractor_rise_off": const.DEFAULT_EXTRACTOR_RISE_OFF,
        "extractor_abs_ceiling": const.DEFAULT_EXTRACTOR_ABS_CEILING,
    }
    s._update_extractor_baseline = zr.ZoneRunner._update_extractor_baseline.__get__(s)
    s._extractor_desired_on = zr.ZoneRunner._extractor_desired_on.__get__(s)
    return s


class _ClockMixin(unittest.TestCase):
    def setUp(self):
        self._now = [datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)]
        self._orig_utcnow = zr._utcnow
        zr._utcnow = lambda: self._now[0]

    def tearDown(self):
        zr._utcnow = self._orig_utcnow

    def feed(self, s, minute, humidity):
        self._now[0] = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)
        s.current_humidity = round(humidity, 1)
        return s._extractor_desired_on()


class ExtractorBaseline(_ClockMixin):
    def test_no_extractor_configured_never_on(self):
        s = _stub()
        s.zone = {}
        s.current_humidity = 90.0
        self.assertFalse(s._extractor_desired_on())

    def test_no_humidity_reading_holds_last_state(self):
        s = _stub(active=True, baseline=51.0)
        s.current_humidity = None
        self.assertTrue(s._extractor_desired_on())  # sigue como estaba
        s2 = _stub(active=False, baseline=51.0)
        s2.current_humidity = None
        self.assertFalse(s2._extractor_desired_on())

    def test_steady_ambient_near_baseline_stays_off(self):
        # Reposo elevado (55%) pero ESTABLE: no debe encender nunca, justo el
        # caso que el umbral absoluto rompia.
        s = _stub(active=False, baseline=None)
        last = False
        for m in range(0, 600, 10):
            last = self.feed(s, m, 55.0)
        self.assertFalse(last)

    def test_shower_spike_turns_on(self):
        s = _stub(active=False, baseline=None)
        # 60 min de reposo a 51 para asentar la base
        for m in range(0, 60):
            self.assertFalse(self.feed(s, m, 51.0))
        # ducha: sube a 69 en 8 min
        turned_on = False
        for i in range(1, 9):
            if self.feed(s, 60 + i, 51 + (69 - 51) * i / 8):
                turned_on = True
                break
        self.assertTrue(turned_on, "la subida brusca de humedad deberia encender el extractor")

    def test_recovery_turns_off(self):
        # Arranca ON (ducha recien detectada), base en el reposo ~51; al
        # recuperar la humedad hacia el reposo tiene que APAGAR.
        s = _stub(active=True, baseline=51.0,
                  baseline_ts=datetime(2026, 10, 1, 7, 55, tzinfo=timezone.utc))
        # secado largo de 69 -> 52
        result = True
        for i in range(0, 60):
            result = self.feed(s, i, 69 - (69 - 52) * i / 59)
        self.assertFalse(result, "al volver la humedad cerca del reposo deberia apagar")

    def test_absolute_ceiling_forces_on_even_without_baseline_rise(self):
        # Base alta (p.ej. arranque del addon con la base aun sin asentar) pero
        # humedad por encima del techo de seguridad -> ON pase lo que pase.
        s = _stub(active=False, baseline=74.0,
                  baseline_ts=datetime(2026, 10, 1, 7, 59, tzinfo=timezone.utc))
        self.assertTrue(self.feed(s, 0, 76.0))

    def test_baseline_barely_moves_during_short_spike(self):
        # La base no debe dejarse arrastrar por un pico corto (si no, el
        # siguiente disparo se mediria contra una base inflada).
        s = _stub(active=False, baseline=None)
        for m in range(0, 120):
            self.feed(s, m, 51.0)
        base_before = s._extractor_baseline
        for i in range(1, 11):  # 10 min de pico a 69
            self.feed(s, 120 + i, 69.0)
        self.assertLess(s._extractor_baseline - base_before, 2.0,
                        "un pico de 10 min no debe mover la base mas de 2 puntos")


if __name__ == "__main__":
    unittest.main()
