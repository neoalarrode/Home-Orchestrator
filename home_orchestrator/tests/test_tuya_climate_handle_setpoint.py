"""El handle climate de tuya_native debe REDONDEAR el setpoint a la resolución
del DP antes de escribirlo. El DP es entero (raw = value * 10**scale); si Climate
calcula una consigna compensada no entera (p.ej. 22.58 = 22 objetivo + 0.58 de
offset de sonda), un termostato de grados enteros (escala 0, qn) truncaba el
float a 22 y perdía TODA la compensación por debajo de 1 grado entero -> el
radiador se quedaba en 22, su sonda lo daba por alcanzado y no calentaba.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from tuya_native.handles import TuyaClimateHandle  # noqa: E402


class _Ctl:
    def __init__(self, state, codes):
        self._state = dict(state)
        self.sets = []

    def get_state(self):
        return dict(self._state)

    def set_dp(self, code, value):
        self.sets.append((code, value))
        self._state[code] = value


def _qn_codes():
    # Radiador qn real: grados enteros (escala 0), codigos capitalizados.
    return {
        "Switch": {"spec": {}},
        "Mode": {"spec": {"range": ["High", "Low"]}},
        "Temp_set": {"spec": {"scale": 0, "min": 15, "max": 30}},
        "Temp_current": {"spec": {"scale": 0}},
    }


def _ac_codes():
    # AC kt con decimas (escala 1): raw = value * 10.
    return {
        "switch": {"spec": {}},
        "mode": {"spec": {"range": ["cold", "hot"]}},
        "temp_set": {"spec": {"scale": 1, "min": 160, "max": 300}},
        "temp_current": {"spec": {"scale": 1}},
    }


class Setpoint(unittest.TestCase):
    def test_integer_dp_rounds_up_compensated_target(self):
        codes = _qn_codes()
        ctl = _Ctl({"Switch": True, "Mode": "High", "Temp_set": 22, "Temp_current": 21}, codes)
        h = TuyaClimateHandle(ctl, codes)
        h.set_temperature(22.58)  # 22 objetivo + 0.58 de compensacion de offset
        # Antes mandaba el float crudo 22.58 -> truncado a 22. Ahora redondea a 23.
        self.assertEqual(ctl.sets, [("Temp_set", 23)])
        self.assertIsInstance(ctl.sets[0][1], int)

    def test_integer_dp_rounds_down_when_below_half(self):
        codes = _qn_codes()
        ctl = _Ctl({"Switch": True, "Temp_set": 22, "Temp_current": 22}, codes)
        h = TuyaClimateHandle(ctl, codes)
        h.set_temperature(22.2)
        self.assertEqual(ctl.sets, [("Temp_set", 22)])

    def test_scale_one_dp_keeps_tenths(self):
        codes = _ac_codes()
        ctl = _Ctl({"switch": True, "mode": "cold", "temp_set": 220, "temp_current": 230}, codes)
        h = TuyaClimateHandle(ctl, codes)
        h.set_temperature(22.58)
        # raw = round(22.58 * 10) = 226 -> 22.6 C
        self.assertEqual(ctl.sets, [("temp_set", 226)])


if __name__ == "__main__":
    unittest.main()
