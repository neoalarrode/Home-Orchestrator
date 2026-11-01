"""Al arrancar, con los equipos aun sin leer (cache de HA y puentes cargando),
la zona no anuncia solo ["off"]: HomeKit construia el termostato con eso en cada
reinicio de HO ("Cannot map hvac target mode: heat_cool ... only off")."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402


class _Stub:
    def __init__(self, readable, known=None):
        self.zone = {zr.CONF_CLIMATE_ENTITIES: ["tuya:ac"]}
        self.zone_id = "z"
        self._readable = readable
        self._known_hvac_modes = known
        self._published_modes_sig = None
        self.mqtt = None
        self._manual_fan_mode = self._manual_swing_mode = self._swing_mode = None
        for n in ("_refresh_hvac_modes", "_compute_capability"):
            setattr(self, n, getattr(zr.ZoneRunner, n).__get__(self))
    def _get_state(self, e):
        return {"state": "heat", "attributes": {"hvac_modes": ["off", "heat", "cool", "dry", "fan_only"]}} if self._readable else None
    def _available_fan_modes(self): return None
    def _available_swing_modes(self): return None


class ModesAtStartup(unittest.TestCase):
    def test_unreadable_with_known_modes_keeps_them(self):
        s = _Stub(readable=False, known=["off", "heat_cool", "heat", "cool", "dry", "fan_only"])
        s._refresh_hvac_modes()
        self.assertIn("heat_cool", s.hvac_modes)

    def test_unreadable_without_known_falls_back_to_full_discovery(self):
        s = _Stub(readable=False)
        s._refresh_hvac_modes()
        self.assertEqual(s.hvac_modes, [])          # publish_discovery usa el respaldo completo

    def test_readable_learns_the_known_modes(self):
        s = _Stub(readable=True)
        s._refresh_hvac_modes()
        self.assertIn("heat_cool", s.hvac_modes)
        self.assertEqual(s._known_hvac_modes, s.hvac_modes)


if __name__ == "__main__":
    unittest.main()
