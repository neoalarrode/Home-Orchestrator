"""Eficiencia sin perder confort:

A. Bomba de calor primero: en una zona con AC (bomba de calor) y radiador
   electrico, al calentar solo trabaja el AC; el radiador entra de apoyo si la
   zona va lejos de la consigna, si no hay AC utilizable o si el AC no la sube.
B. En prioridad "confort" la previsión no rebaja la consigna del usuario (solo
   en "ahorro").
"""
import os, sys, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402
from climate import scheduler  # noqa: E402

AC = {"state": "heat", "attributes": {"hvac_modes": ["off", "heat", "cool", "dry", "fan_only"]}}
RAD = {"state": "heat", "attributes": {"hvac_modes": ["off", "heat"]}}
T0 = datetime(2026, 10, 31, 7, 0, tzinfo=timezone.utc)


class _Stub:
    def __init__(self, states):
        self._states = states
        self.zone = {"name": "Dormitorio", "climate_entities": list(states)}
        self.reason = ""
        self._heat_run = None
        self._delegate_override_until = {}
        self.reserve = zr.ZoneRunner._heat_reserve.__get__(self)
    def _get_state(self, e): return self._states[e]


class _Clock:
    def __init__(self): self.now = T0; self._o = zr._utcnow
    def __enter__(self): zr._utcnow = lambda: self.now; return self
    def __exit__(self, *a): zr._utcnow = self._o


class HeatPumpFirst(unittest.TestCase):
    def test_radiator_in_reserve_while_ac_heats(self):
        with _Clock():
            s = _Stub({"ac": AC, "rad": RAD})
            self.assertEqual(s.reserve("heat", 20.6, urgent=False, safety_emergency=False), {"rad"})
            self.assertIn("radiador en reserva", s.reason)

    def test_radiator_helps_when_far_from_target(self):
        with _Clock():
            s = _Stub({"ac": AC, "rad": RAD})
            self.assertEqual(s.reserve("heat", 19.5, urgent=True, safety_emergency=False), set())

    def test_radiator_helps_when_ac_does_not_raise_the_zone(self):
        with _Clock() as c:
            s = _Stub({"ac": AC, "rad": RAD})
            s.reserve("heat", 20.6, False, False)
            c.now += timedelta(minutes=31)
            self.assertEqual(s.reserve("heat", 20.7, False, False), set())   # +0.1 en 31 min
            c.now += timedelta(minutes=5)
            self.assertEqual(s.reserve("heat", 21.0, False, False), set())   # sigue el apoyo en este tramo

    def test_no_backup_if_ac_is_raising_the_zone(self):
        with _Clock() as c:
            s = _Stub({"ac": AC, "rad": RAD})
            s.reserve("heat", 20.4, False, False)
            c.now += timedelta(minutes=31)
            self.assertEqual(s.reserve("heat", 20.8, False, False), {"rad"})

    def test_radiator_helps_when_ac_unavailable(self):
        with _Clock():
            s = _Stub({"ac": None, "rad": RAD})
            self.assertEqual(s.reserve("heat", 20.6, False, False), set())

    def test_radiator_helps_when_ac_in_manual_override(self):
        with _Clock() as c:
            s = _Stub({"ac": AC, "rad": RAD})
            s._delegate_override_until["ac"] = c.now + timedelta(minutes=20)
            self.assertEqual(s.reserve("heat", 20.6, False, False), set())

    def test_radiator_only_zone_unaffected(self):
        with _Clock():
            s = _Stub({"rad": RAD})
            self.assertEqual(s.reserve("heat", 20.0, False, False), set())
            self.assertEqual(s.reason, "")

    def test_new_heating_run_starts_without_backup(self):
        with _Clock() as c:
            s = _Stub({"ac": AC, "rad": RAD})
            s.reserve("heat", 19.5, urgent=True, safety_emergency=False)      # apoyo
            s.reserve("idle", 21.0, False, False)                             # termina el tramo
            c.now += timedelta(minutes=40)
            self.assertEqual(s.reserve("heat", 20.6, False, False), {"rad"})  # tramo nuevo: reserva


def _decide(priority):
    # Caso real del dormitorio 31-oct (ya con el exterior saneado no modularia, asi
    # que aqui se fuerza una previsión calida para que la modulacion SI actue).
    return scheduler.decide_action(
        current_temp=21.0, heat_target=22.0, cool_target=None, priority=priority, deadband=0.3,
        min_temp=16.0, max_temp=30.0, outdoor_now=28.0, outdoor_forecast=[28.0, 28.0, 28.0],
        heating_rate_deg_h=0.9, cooling_rate_deg_h=0.0, idle_loss_coeff=0.08,
        grid_tier=None, solar_surplus_now_w=None, battery_discharge_headroom_now_w=None,
        zone_estimated_power_w=None, grid_forecast=None,
        occupancy_now_likely=None, occupancy_forecast_likely=None,
    )


class ModulationOnlyInSaving(unittest.TestCase):
    def test_confort_keeps_user_setpoint(self):
        action, reason = _decide("confort")
        self.assertEqual(action, "heat")          # 21.0 < 22 - 0.3: calienta hacia 22
        self.assertIn("22.0", reason)
        self.assertNotIn("previsión exterior sube", reason)

    def test_ahorro_still_modulates(self):
        action, reason = _decide("ahorro")
        self.assertIn("previsión exterior sube", reason)


if __name__ == "__main__":
    unittest.main()
