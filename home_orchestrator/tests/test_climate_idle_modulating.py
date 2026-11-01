"""Idle MODULANDO de las bombas de calor (decision del usuario, 31-oct): con la
zona en banda, el AC se queda en su modo con la consigna de la zona (compensada)
para que el inverter module, en vez de aparcarlo al minimo/maximo. Si la zona se
pasa, se aparca; vuelve a mantener al regresar a la consigna. Los radiadores, la
ventana y la reserva siguen aparcando."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402
import climate.power_model as pm  # noqa: E402

AC = ["off", "heat", "cool", "dry", "fan_only"]
RAD = ["off", "heat"]


class _Stub:
    def __init__(self, modes=AC, dev_temp=21.0, setpoint=23.0, mode="heat"):
        self.state = {"state": mode, "attributes": {"hvac_modes": list(modes), "current_temperature": dev_temp,
                                                    "temperature": setpoint, "min_temp": 16.0, "max_temp": 30.0}}
        self.hvac_mode = "heat_cool"
        self.target_temperature_low, self.target_temperature_high = 21.0, 25.0
        self.zone = {"name": "Salón", "min_temp": 16.0, "max_temp": 30.0}
        self.calls = []
        self._deviation_at_rest = set(); self._hold_run = {}; self._hold_trim = {}; self._rest_dev_at = {}
        for a in ("_delegate_last_command", "_delegate_last_active", "_delegate_deviations",
                  "_activation_probe", "_delegate_pre_temp", "_park_since", "_hold_overshoot"):
            setattr(self, a, {})
        self._delegate_last_active["e"] = (mode, 21.0)
        for n in ("_drive_climate_idle", "_send_delegate_once", "_note_delegate_command",
                  "_compensate_delegate_target", "_fallback_thermal_mode", "_learn_rest_deviation", "_maintain_heat_pump", "_side_target", "_learn_hold_trim", "_bump_hold_trim"):
            setattr(self, n, getattr(zr.ZoneRunner, n).__get__(self))
    def _get_state(self, e): return self.state
    def _respect_delegate_override(self, *a): return False
    def _delegate_send_allowed(self, *a): return True
    def _drive_delegate_fan_mode(self, *a, **k): pass
    def _call_climate_service(self, e, service, data=None):
        self.calls.append((service, data))
        if service == "set_temperature":
            self.state["attributes"]["temperature"] = data["temperature"]
    def temps(self): return [d["temperature"] for sv, d in self.calls if sv == "set_temperature"]


class IdleModulating(unittest.TestCase):
    def test_heat_pump_in_band_holds_zone_setpoint(self):
        s = _Stub()
        self.assertEqual(s._drive_climate_idle("e", 21.0, 0.3, False, maintain=True), "hold")
        self.assertEqual(s.temps(), [21.0])          # consigna de zona (+ desviacion 0 aqui), no 16

    def test_overshoot_parks_and_resumes_at_setpoint(self):
        s = _Stub()
        s._drive_climate_idle("e", 21.4, 0.3, False, maintain=True)   # se ha pasado: aparca
        self.assertEqual(s.temps(), [16.0])
        s.calls.clear()
        self.assertEqual(s._drive_climate_idle("e", 21.1, 0.3, False, maintain=True), "idle")  # aun por encima
        self.assertEqual(s.temps(), [])
        self.assertEqual(s._drive_climate_idle("e", 21.0, 0.3, False, maintain=True), "hold")  # vuelve
        self.assertEqual(s.temps(), [21.0])

    def test_cooling_side(self):
        s = _Stub(mode="cool", setpoint=30.0)
        s._delegate_last_active["e"] = ("cool", 25.0)
        self.assertEqual(s._drive_climate_idle("e", 25.0, 0.3, False, maintain=True), "hold")
        self.assertEqual(s.temps(), [25.0])

    def test_radiator_still_parks(self):
        s = _Stub(modes=RAD)
        self.assertEqual(s._drive_climate_idle("e", 21.0, 0.3, False, maintain=True), "idle")
        self.assertEqual(s.temps(), [16.0])

    def test_without_maintain_parks_as_before(self):
        # ventana abierta / reserva del radiador / zona en dry fijado a mano
        s = _Stub()
        self.assertEqual(s._drive_climate_idle("e", 21.0, 0.3, False, maintain=False), "idle")
        self.assertEqual(s.temps(), [16.0])

    def test_hold_is_not_a_power_start(self):
        log = [(1000.0, "hold"), (2000.0, "heat"), (3000.0, "off"), (4000.0, "heat")]
        starts = [t.timestamp() for t in pm._zone_starts(log, 100000)]
        self.assertEqual(starts, [4000.0])


class HoldTrim(unittest.TestCase):
    """Caso real 1-nov (dormitorio): mantener -> la zona se pasa en 5 min, cada
    1.5 h. Cada rafaga recorta la consigna de mantener; un mantener que no llega
    la sube. Solo con el sensor de zona."""
    def _clock(self, start):
        t = [start]
        zr._utcnow = lambda: t[0]
        return t

    def setUp(self):
        self._orig = zr._utcnow

    def tearDown(self):
        zr._utcnow = self._orig

    def test_quick_overshoot_trims_next_hold(self):
        from datetime import datetime, timedelta, timezone
        t = self._clock(datetime(2026, 11, 1, 2, 25, tzinfo=timezone.utc))
        s = _Stub()
        s._delegate_deviations["e"] = 3.0; s._deviation_at_rest.add("e")
        s._drive_climate_idle("e", 21.0, 0.3, False, maintain=True)
        self.assertEqual(s.temps(), [24.0])                 # 21 + 3
        t[0] += timedelta(minutes=5)
        s._drive_climate_idle("e", 21.4, 0.3, False, maintain=True)   # rafaga: aparca
        self.assertEqual(s._hold_trim["e"], 0.5)
        s.calls.clear()
        t[0] += timedelta(minutes=90)
        s._drive_climate_idle("e", 21.0, 0.3, False, maintain=True)
        self.assertEqual(s.temps(), [23.5])                 # 21 + 3 - 0.5

    def test_long_hold_without_reaching_raises(self):
        from datetime import datetime, timedelta, timezone
        t = self._clock(datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc))
        s = _Stub()
        s._hold_trim["e"] = 1.0
        s._drive_climate_idle("e", 21.0, 0.3, False, maintain=True)
        for _ in range(10):
            t[0] += timedelta(minutes=10)
            s._drive_climate_idle("e", 20.8, 0.3, False, maintain=True)
        self.assertEqual(s._hold_trim["e"], 0.5)

    def test_slow_overshoot_does_not_trim(self):
        from datetime import datetime, timedelta, timezone
        t = self._clock(datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc))
        s = _Stub()
        s._drive_climate_idle("e", 21.0, 0.3, False, maintain=True)
        t[0] += timedelta(minutes=45)
        s._drive_climate_idle("e", 21.3, 0.3, False, maintain=True)
        self.assertNotIn("e", s._hold_trim)

    def test_cooling_trim_raises_setpoint(self):
        from datetime import datetime, timedelta, timezone
        self._clock(datetime(2026, 11, 1, 14, 0, tzinfo=timezone.utc))
        s = _Stub(mode="cool", setpoint=30.0)
        s._delegate_last_active["e"] = ("cool", 25.0)
        s._hold_trim["e"] = 1.0
        s._drive_climate_idle("e", 25.0, 0.3, False, maintain=True)
        self.assertEqual(s.temps(), [26.0])


class RestDeviationIsSlow(unittest.TestCase):
    def test_time_based_smoothing(self):
        from datetime import datetime, timedelta, timezone
        orig = zr._utcnow
        t = [datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)]
        zr._utcnow = lambda: t[0]
        try:
            s = _Stub()
            s._delegate_deviations["e"] = 1.0; s._deviation_at_rest.add("e")
            s._rest_dev_at["e"] = t[0]
            s._park_since["e"] = t[0] - timedelta(hours=1)
            for _ in range(20):                              # 20 min leyendo +3
                t[0] += timedelta(minutes=1)
                s._learn_rest_deviation("e", {"current_temperature": 24.0}, 21.0)
            # antes (0.1 por ciclo) ya iba por 2.76; ahora apenas se mueve
            self.assertLess(s._delegate_deviations["e"], 1.4)
        finally:
            zr._utcnow = orig


if __name__ == "__main__":
    unittest.main()
