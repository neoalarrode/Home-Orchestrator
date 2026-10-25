"""IDLE de un climate delegado: no se apaga ni cambia de modo para quedarse quieto;
'a tope' SIN fiarse de la sonda del equipo -> aparca la consigna en el LIMITE del
termostato de ZONA (calor -> min_temp de zona, frio -> max_temp de zona). Mantiene
su modo. El unico off es el manual del usuario. Aplica a cualquier zona/AC.
"""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402


class _Stub:
    def __init__(self, state, last_active=None, override=False, zmin=16.0, zmax=30.0):
        self._state = state
        self._delegate_last_active = dict(last_active or {})
        self._override = override
        self.calls = []
        self.zone = {"name": "Z", "min_temp": zmin, "max_temp": zmax}
        self._drive_climate_idle = zr.ZoneRunner._drive_climate_idle.__get__(self)
    def _get_state(self, e): return self._state
    def _respect_delegate_override(self, e, s, se): return self._override
    def _delegate_send_allowed(self, e, now): return True
    def _call_climate_service(self, e, service, data=None): self.calls.append((service, data))
    def _note_delegate_command(self, *a, **k): pass


def _st(mode, cur, target, modes=("off","heat","cool","dry","fan_only"), **extra):
    return {"state": mode, "attributes": {"hvac_modes": list(modes),
            "current_temperature": cur, "temperature": target, **extra}}


class IdlePark(unittest.TestCase):
    def test_heat_parks_at_zone_min(self):
        s = _Stub(_st("heat", 30.0, 22.0), last_active={"e": ("heat", 22.0)}, zmin=16.0, zmax=30.0)
        s._drive_climate_idle("e", 27.0, 0.3, False)
        temps = [d["temperature"] for sv, d in s.calls if sv == "set_temperature"]
        self.assertEqual(temps, [16.0])  # minimo de zona, ignora la sonda (30)

    def test_cool_parks_at_zone_max(self):
        s = _Stub(_st("cool", 20.0, 24.0), last_active={"e": ("cool", 24.0)}, zmin=16.0, zmax=30.0)
        s._drive_climate_idle("e", 22.0, 0.3, False)
        temps = [d["temperature"] for sv, d in s.calls if sv == "set_temperature"]
        self.assertEqual(temps, [30.0])  # maximo de zona

    def test_never_turns_off(self):
        s = _Stub(_st("cool", 24.0, 20.0), last_active={"e": ("cool", 20.0)})
        s._drive_climate_idle("e", 24.0, 0.3, False)
        self.assertNotIn("off", [d.get("hvac_mode") for sv, d in s.calls if sv == "set_hvac_mode"])

    def test_manual_off_respected(self):
        s = _Stub(_st("off", 24.0, 20.0), last_active={"e": ("cool", 20.0)})
        s._drive_climate_idle("e", 24.0, 0.3, False)
        self.assertEqual(s.calls, [])

    def test_returns_from_dry_to_base_mode(self):
        s = _Stub(_st("dry", 23.0, 18.0), last_active={"e": ("cool", 22.0)}, zmax=30.0)
        s._drive_climate_idle("e", 23.0, 0.3, False)
        modes = [d["hvac_mode"] for sv, d in s.calls if sv == "set_hvac_mode"]
        temps = [d["temperature"] for sv, d in s.calls if sv == "set_temperature"]
        self.assertEqual(modes, ["cool"]); self.assertEqual(temps, [30.0])

    def test_user_override_backs_off(self):
        s = _Stub(_st("cool", 23.2, 21.0), last_active={"e": ("cool", 21.0)}, override=True)
        s._drive_climate_idle("e", 23.0, 0.3, False)
        self.assertEqual(s.calls, [])

    def test_park_respects_delegate_declared_minimum(self):
        # Zona con minimo 15, AC con minimo 16: aparcar en 16 (un 15 lo rechaza).
        s = _Stub(_st("heat", 28.0, 23.0, min_temp=16.0, max_temp=88.0, target_temp_step=0.5),
                  last_active={"e": ("heat", 23.0)}, zmin=15.0, zmax=30.0)
        s._drive_climate_idle("e", 23.4, 0.3, False)
        temps = [d["temperature"] for sv, d in s.calls if sv == "set_temperature"]
        self.assertEqual(temps, [16.0])

    def test_already_at_device_minimum_does_not_resend(self):
        s = _Stub(_st("heat", 28.0, 16.0, min_temp=16.0, max_temp=88.0),
                  last_active={"e": ("heat", 23.0)}, zmin=15.0, zmax=30.0)
        s._drive_climate_idle("e", 23.4, 0.3, False)
        self.assertEqual([c for c in s.calls if c[0] == "set_temperature"], [])


if __name__ == "__main__":
    unittest.main()
