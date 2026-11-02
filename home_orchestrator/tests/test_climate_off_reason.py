"""Una zona solo de extractor/humedad no dice "apagado desde el termostato"
(2-nov, Baño Arriba: parecia que alguien la habia apagado)."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402


class _S:
    def __init__(self, zone):
        self.zone = zone
        self._off_reason = zr.ZoneRunner._off_reason.__get__(self)


class OffReason(unittest.TestCase):
    def test_extractor_only_zone(self):
        r = _S({"extractor_switches": ["switch.v"], "heat_switches": [], "climate_entities": []})._off_reason()
        self.assertIn("nada que climatizar", r)

    def test_zone_with_heating_turned_off(self):
        self.assertEqual(_S({"heat_switches": ["switch.radiador"]})._off_reason(), "apagado desde el termostato")
        self.assertEqual(_S({"climate_entities": ["climate.ac"]})._off_reason(), "apagado desde el termostato")


if __name__ == "__main__":
    unittest.main()
