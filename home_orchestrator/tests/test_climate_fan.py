"""_pick_fan_mode: en marcha normal (no urgente) manda AUTO, nunca la velocidad
minima (calentar/enfriar al minimo recalienta la sonda del equipo y va lento).
Urgente -> la mas fuerte; manual del usuario -> se respeta; sin 'auto' -> cae al
mas suave real como ultimo recurso.
"""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from climate.zone_runner import _pick_fan_mode  # noqa: E402

AC = ["strong", "high", "mid_high", "mid", "mid_low", "low", "mute", "auto"]


class PickFan(unittest.TestCase):
    def test_normal_is_auto(self):
        self.assertEqual(_pick_fan_mode(AC, urgent=False, manual=None), "auto")

    def test_urgent_is_strongest(self):
        self.assertEqual(_pick_fan_mode(AC, urgent=True, manual=None), "strong")

    def test_manual_wins(self):
        self.assertEqual(_pick_fan_mode(AC, urgent=False, manual="mid"), "mid")

    def test_no_auto_falls_back_to_softest_real(self):
        no_auto = ["strong", "high", "mid_high", "mid", "mid_low", "low"]
        self.assertEqual(_pick_fan_mode(no_auto, urgent=False, manual=None), "low")  # no "mid_low"

    def test_empty(self):
        self.assertIsNone(_pick_fan_mode([], urgent=False, manual=None))


if __name__ == "__main__":
    unittest.main()
