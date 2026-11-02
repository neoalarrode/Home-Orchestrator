"""zone_power_w de una zona con aire de puente (sin potencia propia): la potencia
aprendida de la zona mientras calienta/enfria; nada en reposo (el aire aparcado
en calor a 16 C cuenta como "activo" pero no gasta)."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402


class _Stub:
    def __init__(self, action, learned=529.0, reliable=True, extra=None):
        self.hvac_action = action
        self._zone_power = {"learned_power_w": learned, "reliable": reliable}
        self.actuators = ["tuya:ac"] + list(extra or {})
        self.extra = extra or {}
        self.f = zr.ZoneRunner._zone_power_w.__get__(self)
    def _climate_actuators(self): return self.actuators
    def _actuator_active(self, e): return True
    def _actuator_power_w(self, e): return (self.extra[e], "measured") if e in self.extra else (None, "none")


class ZonePowerLearned(unittest.TestCase):
    def test_heating_uses_learned(self):
        w, bd = _Stub("heating").f()
        self.assertEqual(w, 529.0)
        self.assertEqual(bd["zone"]["source"], "learned_zone")

    def test_idle_parked_ac_counts_nothing(self):
        self.assertEqual(_Stub("idle").f(), (None, {}))

    def test_unreliable_learning_is_not_used(self):
        self.assertEqual(_Stub("heating", reliable=False).f(), (None, {}))

    def test_known_actuator_plus_learned(self):
        w, _ = _Stub("heating", extra={"switch.rad": 800.0}).f()
        self.assertEqual(w, 1329.0)

    def test_idle_with_known_actuator_keeps_measured(self):
        self.assertEqual(_Stub("idle", extra={"switch.rad": 0.7}).f()[0], 0.7)


if __name__ == "__main__":
    unittest.main()
