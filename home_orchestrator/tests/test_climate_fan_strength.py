"""Ventilador en urgencia por FUERZA, no por posicion (hallazgo R1#2)."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402

REAL = ['strong', 'high', 'mid_high', 'mid', 'mid_low', 'low', 'mute', 'auto']  # AirClima real


class FanStrength(unittest.TestCase):
    def test_real_airclima_unchanged(self):
        self.assertEqual(zr._pick_fan_mode(REAL, True, None), "strong")
        self.assertEqual(zr._pick_fan_mode(REAL, False, None), "auto")

    def test_order_independent(self):
        self.assertEqual(zr._pick_fan_mode(['low', 'mid_high', 'high', 'strong'], True, None), "strong")
        self.assertEqual(zr._pick_fan_mode(['auto', 'low', 'high', 'mid_high'], True, None), "high")

    def test_numeric_modes_pick_the_highest(self):
        self.assertEqual(zr._pick_fan_mode(['1', '2', '3', '4', '5'], True, None), "5")
        self.assertEqual(zr._pick_fan_mode(['auto', '1', '2', '3'], True, None), "3")

    def test_manual_wins(self):
        self.assertEqual(zr._pick_fan_mode(REAL, True, "low"), "low")


if __name__ == "__main__":
    unittest.main()
