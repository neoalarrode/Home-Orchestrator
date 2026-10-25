"""Camino ACTIVO de un climate delegado (demanda real de calor/frio): la sonda
interna del equipo se usa SOLO para calcular la desviacion de calibracion contra
NUESTRO sensor de zona, y esa desviacion compensa la consigna. La desviacion se
trata como calibracion ESTABLE: cuando el equipo calienta y su sonda se contamina
(|desviacion| > CALIB_BAND_DEG) la muestra se rechaza y la desviacion se congela,
asi la consigna no se dispara (antes: rampa 23->30 por realimentacion positiva).
En idle la sonda interna NO se usa (eso lo cubre test_climate_idle_park).
"""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402


class _Stub:
    def __init__(self, state, override=False):
        self._state = state
        self._override = override
        self.calls = []
        self.zone = {"name": "Z", "min_temp": 15.0, "max_temp": 30.0}
        self._delegate_last_active = {}
        self._delegate_deviations = {}
        self._drive_climate_actuator = zr.ZoneRunner._drive_climate_actuator.__get__(self)
        self._compensate_delegate_target = zr.ZoneRunner._compensate_delegate_target.__get__(self)
    def _get_state(self, e): return self._state
    def _respect_delegate_override(self, e, s, se): return self._override
    def _delegate_send_allowed(self, e, now): return True
    def _call_climate_service(self, e, service, data=None): self.calls.append((service, data))
    def _note_delegate_command(self, *a, **k): pass
    def _drive_delegate_fan_mode(self, *a, **k): pass


def _st(mode, cur, target, modes=("off", "heat", "cool", "dry", "fan_only"), mn=15.0, mx=30.0):
    return {"state": mode, "attributes": {"hvac_modes": list(modes),
            "current_temperature": cur, "temperature": target, "min_temp": mn, "max_temp": mx}}


class ActuatorCompensation(unittest.TestCase):
    def test_compensates_with_stable_deviation(self):
        # Sonda del equipo +0.6 sobre la zona: consigna = objetivo + 0.6.
        s = _Stub(_st("heat", 20.6, 23.0))
        s._drive_climate_actuator("e", "heat", 23.0, 20.0, simulate=False)
        temps = [d["temperature"] for sv, d in s.calls if sv == "set_temperature"]
        self.assertEqual(len(temps), 1)
        # Primera muestra: desviacion cruda 0.6 (sin EMA aun) -> 23.0 + 0.6.
        self.assertAlmostEqual(temps[0], 23.6, places=2)

    def test_no_runaway_when_device_sensor_climbs(self):
        # El equipo calienta y SU sonda sube ~1C/min hasta +10 sobre la zona.
        # La desviacion debe quedarse pegada a la calibracion estable (~0.5) y la
        # consigna NO debe escalar: antes subia 23->24->...->30.
        sent = []
        dev_state = {}
        device_sensor = 20.5   # arranca +0.5 sobre la zona (20.0): calibracion real
        for _ in range(14):
            cur_target = sent[-1] if sent else 23.0
            s = _Stub(_st("heat", device_sensor, cur_target))
            s._delegate_deviations = dev_state          # desviacion persistente entre ciclos
            s._drive_climate_actuator("e", "heat", 23.0, 20.0, simulate=False)
            for sv, d in s.calls:
                if sv == "set_temperature":
                    sent.append(d["temperature"])
            device_sensor += 1.0                        # auto-calentamiento de la sonda
        # Nunca supera objetivo + banda de calibracion (23 + 2.0): sin runaway.
        self.assertLessEqual(max(sent), 23.0 + zr.CALIB_BAND_DEG + 0.001, sent)
        self.assertLess(max(sent), 26.0, sent)          # muy lejos del 30 del runaway

    def test_cool_compensation_sign(self):
        # En frio, sonda -0.5 bajo la zona -> consigna = objetivo - algo.
        s = _Stub(_st("cool", 24.5, 23.0))
        s._drive_climate_actuator("e", "cool", 24.0, 25.0, simulate=False)
        temps = [d["temperature"] for sv, d in s.calls if sv == "set_temperature"]
        self.assertEqual(len(temps), 1)
        self.assertLess(temps[0], 24.0)                 # desviacion negativa baja la consigna

    def test_contaminated_sample_is_rejected(self):
        # Primera muestra creible fija ~0.5; una muestra contaminada (+8) no la mueve.
        dev = {}
        s1 = _Stub(_st("heat", 20.5, 23.0)); s1._delegate_deviations = dev
        s1._drive_climate_actuator("e", "heat", 23.0, 20.0, simulate=False)
        base = dev["e"]
        s2 = _Stub(_st("heat", 28.0, 23.0)); s2._delegate_deviations = dev  # sonda +8 (contaminada)
        s2._drive_climate_actuator("e", "heat", 23.0, 20.0, simulate=False)
        self.assertEqual(dev["e"], base)                # congelada, no saltó

    def test_simulate_sends_nothing_but_learns(self):
        s = _Stub(_st("heat", 20.6, 23.0))
        s._drive_climate_actuator("e", "heat", 23.0, 20.0, simulate=True)
        self.assertEqual([c for c in s.calls], [])      # no manda nada
        self.assertIn("e", s._delegate_deviations)      # pero aprende la desviacion


if __name__ == "__main__":
    unittest.main()
