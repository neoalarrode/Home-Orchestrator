"""Presets sin banda entre calor y frio en una zona calor+frio (hallazgo R2#E):
un solo valor ("Confort: 21") o calor=frio dejaban la zona en reposo para
siempre. Ahora se separan alrededor del valor pedido."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402


class _Stub:
    def __init__(self, presets):
        self._presets = presets
        self.zone = {"deadband": 0.3}
        self._manual_heat = self._manual_cool = None
        self.resolve = zr.ZoneRunner._resolve_preset_targets.__get__(self)
        self._preset_value = zr.ZoneRunner._preset_value.__get__(self)


class PresetBand(unittest.TestCase):
    def test_single_value_in_heat_cool_gets_a_band(self):
        s = _Stub([{"name": "Confort", "heat_temp": 21.0, "cool_temp": 21.0}])
        self.assertEqual(s.resolve("Confort", True, True), (20.7, 21.3))

    def test_normal_pair_untouched(self):
        s = _Stub([{"name": "Confort", "heat_temp": 21.0, "cool_temp": 25.0}])
        self.assertEqual(s.resolve("Confort", True, True), (21.0, 25.0))

    def test_single_direction_zone_untouched(self):
        s = _Stub([{"name": "Confort", "heat_temp": 20.0, "cool_temp": 20.0}])
        self.assertEqual(s.resolve("Confort", True, False), (20.0, None))


if __name__ == "__main__":
    unittest.main()
