"""5-nov: una bomba de calor manteniendo (modulando) sin medidor propio
publicaba 0 W y Energy no reservaba bateria para ella. Ahora se estima con el
modelo termico: lo que da es lo que pierde la zona (decision del usuario)."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402


class _S:
    def __init__(self, thermal, temp=21.0, outdoor=8.7, power=961.0):
        self._thermal_model = thermal
        self.current_temperature = temp
        self._outdoor_now = outdoor
        self._zone_power = {}
        self.zone_power_for = lambda mode, ext: power
        self._hold_power_estimate_w = zr.ZoneRunner._hold_power_estimate_w.__get__(self)


DORM = {"reliable": True, "heating_rate_deg_h": 0.9, "cooling_rate_deg_h": 1.2, "idle_loss_coeff": 0.097}


class HoldPower(unittest.TestCase):
    def test_real_dormitorio_case(self):
        loss = 0.097 * (21.0 - 8.7)
        self.assertAlmostEqual(_S(DORM)._hold_power_estimate_w(), round(961 * loss / (loss + 0.9), 1), delta=0.2)

    def test_milder_outside_costs_less(self):
        self.assertLess(_S(DORM, outdoor=15.0)._hold_power_estimate_w(), _S(DORM)._hold_power_estimate_w())

    def test_cooling_uses_cooling_rate(self):
        w = _S(DORM, temp=25.0, outdoor=33.0)._hold_power_estimate_w()
        loss = 0.097 * 8.0
        self.assertAlmostEqual(w, round(961 * loss / (loss + 1.2), 1), delta=0.2)

    def test_without_model_or_power_is_none(self):
        self.assertIsNone(_S({"reliable": False})._hold_power_estimate_w())
        self.assertIsNone(_S(DORM, outdoor=None)._hold_power_estimate_w())
        self.assertIsNone(_S(DORM, power=None)._hold_power_estimate_w())


if __name__ == "__main__":
    unittest.main()
