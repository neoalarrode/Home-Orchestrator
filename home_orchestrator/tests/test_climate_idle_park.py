"""IDLE de un climate delegado: no se apaga ni cambia de modo para quedarse quieto;
se aparca la consigna en la temperatura que el propio equipo lee (<= en calor, >=
en frio) manteniendo su modo. El unico off es el manual del usuario.
"""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402


class _Stub:
    def __init__(self, state, last_active=None, override=False):
        self._state = state
        self._delegate_last_active = dict(last_active or {})
        self._override = override
        self.calls = []
        self._drive_climate_idle = zr.ZoneRunner._drive_climate_idle.__get__(self)
        self.zone = {"name": "Z"}
    def _get_state(self, e): return self._state
    def _respect_delegate_override(self, e, s, se): return self._override
    def _delegate_send_allowed(self, e, now): return True
    def _call_climate_service(self, e, service, data=None): self.calls.append((service, data))
    def _note_delegate_command(self, *a, **k): pass


def _st(mode, cur, target, modes=("off","heat","cool","dry","fan_only")):
    return {"state": mode, "attributes": {"hvac_modes": list(modes),
            "current_temperature": cur, "temperature": target}}


class IdlePark(unittest.TestCase):
    def test_cool_parks_at_or_above_reading(self):
        s = _Stub(_st("cool", 23.2, 21.0), last_active={"e": ("cool", 21.0)})
        s._drive_climate_idle("e", current_temp=23.0, deadband=0.3, simulate=False)
        temps = [d["temperature"] for sv, d in s.calls if sv == "set_temperature"]
        self.assertEqual(temps, [23.5])  # ceil(23.2) -> no pide frio
        self.assertFalse([c for c in s.calls if c[0] == "set_hvac_mode" and c[1]["hvac_mode"] == "off"])

    def test_heat_parks_at_or_below_reading(self):
        s = _Stub(_st("heat", 21.3, 23.0), last_active={"e": ("heat", 23.0)})
        s._drive_climate_idle("e", 21.0, 0.3, False)
        temps = [d["temperature"] for sv, d in s.calls if sv == "set_temperature"]
        self.assertEqual(temps, [21.0])  # floor(21.3) -> no pide calor

    def test_never_turns_off(self):
        s = _Stub(_st("cool", 24.0, 20.0), last_active={"e": ("cool", 20.0)})
        s._drive_climate_idle("e", 24.0, 0.3, False)
        self.assertNotIn("off", [d.get("hvac_mode") for sv, d in s.calls if sv == "set_hvac_mode"])

    def test_manual_off_is_respected(self):
        s = _Stub(_st("off", 24.0, 20.0), last_active={"e": ("cool", 20.0)})
        s._drive_climate_idle("e", 24.0, 0.3, False)
        self.assertEqual(s.calls, [])  # no lo encendemos ni tocamos

    def test_returns_from_dry_to_base_mode_parked(self):
        # estaba en dry; el ultimo termico fue cool -> vuelve a cool y aparca
        s = _Stub(_st("dry", 23.0, 18.0), last_active={"e": ("cool", 22.0)})
        s._drive_climate_idle("e", 23.0, 0.3, False)
        modes = [d["hvac_mode"] for sv, d in s.calls if sv == "set_hvac_mode"]
        temps = [d["temperature"] for sv, d in s.calls if sv == "set_temperature"]
        self.assertEqual(modes, ["cool"]); self.assertEqual(temps, [23.0])

    def test_user_override_backs_off(self):
        s = _Stub(_st("cool", 23.2, 21.0), last_active={"e": ("cool", 21.0)}, override=True)
        s._drive_climate_idle("e", 23.0, 0.3, False)
        self.assertEqual(s.calls, [])


if __name__ == "__main__":
    unittest.main()
