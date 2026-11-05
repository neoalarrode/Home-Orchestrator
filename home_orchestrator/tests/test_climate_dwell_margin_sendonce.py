"""Tres protecciones del control de delegados de Climate:

1. Tiempo minimo en un modo de equipo antes de que una decision automatica lo
   cambie (dormitorio saltando calor<->dry cada 3-15 min).
2. Margen de arranque aprendido por equipo (radiador que no arranca sin ~2C de
   hueco sobre su propia sonda).
3. No reenviar la misma orden mientras se espera a que el equipo la refleje.
"""
import os, sys, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402

T0 = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)


class _Clock:
    def __init__(self):
        self.now = T0
        self._orig = zr._utcnow

    def __enter__(self):
        zr._utcnow = lambda: self.now
        return self

    def __exit__(self, *a):
        zr._utcnow = self._orig

    def advance(self, minutes):
        self.now += timedelta(minutes=minutes)


class _DwellStub:
    def __init__(self):
        self.reason = ""
        self._mode_hold = None
        self._last_thermal_mode = None
        self.dwell = zr.ZoneRunner._apply_mode_dwell.__get__(self)


class ModeDwell(unittest.TestCase):
    def test_heat_then_dry_too_soon_stays_parked_in_heat(self):
        with _Clock() as c:
            s = _DwellStub()
            self.assertEqual(s.dwell("heat", False), "heat")
            c.advance(3)
            self.assertEqual(s.dwell("dry", False), "idle")   # aparcado en calor, sin pasar a dry
            self.assertIn("se mantiene en heat", s.reason)

    def test_dry_then_heat_too_soon_keeps_drying(self):
        with _Clock() as c:
            s = _DwellStub()
            s._last_thermal_mode = "heat"
            self.assertEqual(s.dwell("dry", False), "dry")
            c.advance(9)
            self.assertEqual(s.dwell("heat", False), "dry")

    def test_change_allowed_after_dwell(self):
        with _Clock() as c:
            s = _DwellStub()
            s.dwell("heat", False)
            c.advance(16)
            self.assertEqual(s.dwell("dry", False), "dry")

    def test_same_mode_never_blocked(self):
        with _Clock() as c:
            s = _DwellStub()
            s.dwell("heat", False)
            c.advance(2)
            self.assertEqual(s.dwell("idle", False), "idle")  # idle mantiene calor: no es cambio
            c.advance(2)
            self.assertEqual(s.dwell("heat", False), "heat")

    def test_safety_never_waits(self):
        with _Clock() as c:
            s = _DwellStub()
            s._last_thermal_mode = "heat"
            s.dwell("dry", False)
            c.advance(1)
            self.assertEqual(s.dwell("heat", True), "heat")

    def test_user_order_clears_hold(self):
        with _Clock() as c:
            s = _DwellStub()
            s.dwell("heat", False)
            c.advance(1)
            s._mode_hold = None   # lo que hacen set_temperature/set_hvac_mode/set_preset_mode
            self.assertEqual(s.dwell("dry", False), "dry")

    def test_flapping_sequence_from_production_is_damped(self):
        # Dormitorio 25-oct: heat 10:05, dry 10:08, heat 10:22, dry 10:26, heat 10:35, dry 10:37
        with _Clock() as c:
            s = _DwellStub()
            wanted = [(0, "heat"), (3, "dry"), (14, "heat"), (4, "dry"), (9, "heat"), (2, "dry")]
            modes = []
            for gap, want in wanted:
                c.advance(gap)
                act = s.dwell(want, False)
                modes.append(act if act != "idle" else s._last_thermal_mode)
            changes = sum(1 for a, b in zip(modes, modes[1:]) if a != b)
            self.assertLessEqual(changes, 2, modes)  # antes: 5 cambios en 32 min


class _MarginStub:
    def __init__(self):
        self.zone = {"name": "Cuarto"}
        self._delegate_activation_margin = {}
        self._activation_probe = {}
        self.learn = zr.ZoneRunner._learn_activation_margin.__get__(self)
        self._activation_ok_streak = {}
        self._relax_activation_margin = zr.ZoneRunner._relax_activation_margin.__get__(self)


class ActivationMargin(unittest.TestCase):
    def _attrs(self, dev, step=None):
        a = {"current_temperature": dev}
        if step is not None:
            a["target_temp_step"] = step
        return a

    def test_device_that_does_not_fire_gets_margin_in_its_own_step(self):
        s = _MarginStub()
        t = T0
        self.assertEqual(s.learn("e", "heat", self._attrs(22, step=1.0), 20.5, t), 0.0)
        t += timedelta(minutes=21)
        self.assertEqual(s.learn("e", "heat", self._attrs(22, step=1.0), 20.58, t), 1.0)  # radiador entero: +1
        t += timedelta(minutes=21)
        self.assertEqual(s.learn("e", "heat", self._attrs(22, step=1.0), 20.62, t), 2.0)

    def test_device_that_heats_keeps_margin(self):
        s = _MarginStub()
        t = T0
        s.learn("e", "heat", self._attrs(22), 20.5, t)
        t += timedelta(minutes=21)
        self.assertEqual(s.learn("e", "heat", self._attrs(22), 20.9, t), 0.0)

    def test_margin_capped(self):
        s = _MarginStub()
        s._delegate_activation_margin["e"] = zr.ACTIVATION_MARGIN_MAX_DEG
        t = T0
        s.learn("e", "heat", self._attrs(22, step=1.0), 20.5, t)
        t += timedelta(minutes=21)
        self.assertEqual(s.learn("e", "heat", self._attrs(22, step=1.0), 20.5, t), zr.ACTIVATION_MARGIN_MAX_DEG)

    def test_cooling_direction(self):
        s = _MarginStub()
        t = T0
        s.learn("e", "cool", self._attrs(27), 26.0, t)
        t += timedelta(minutes=21)
        self.assertEqual(s.learn("e", "cool", self._attrs(27), 26.0, t), 0.5)   # no baja: margen
        t += timedelta(minutes=21)
        self.assertEqual(s.learn("e", "cool", self._attrs(26), 25.5, t), 0.5)   # baja: se queda

    def test_probe_restarts_when_action_changes(self):
        s = _MarginStub()
        t = T0
        s.learn("e", "heat", self._attrs(22), 20.5, t)
        t += timedelta(minutes=21)
        self.assertEqual(s.learn("e", "cool", self._attrs(22), 20.5, t), 0.0)  # nueva sonda, sin juzgar


class _SendStub:
    def __init__(self):
        self.zone = {"name": "Z"}
        self.calls = []
        self._delegate_last_command = {}
        self.send = zr.ZoneRunner._send_delegate_once.__get__(self)
        self._note_delegate_command = zr.ZoneRunner._note_delegate_command.__get__(self)

    def _call_climate_service(self, e, service, data=None):
        self.calls.append((service, data))


class SendOnce(unittest.TestCase):
    def test_same_value_not_resent_while_waiting(self):
        s = _SendStub()
        self.assertTrue(s.send("e", "temperature", 16.0, "set_temperature", {"temperature": 16.0}, T0))
        self.assertFalse(s.send("e", "temperature", 16.0, "set_temperature", {"temperature": 16.0}, T0 + timedelta(minutes=2)))
        self.assertEqual(len(s.calls), 1)

    def test_new_value_sent_immediately(self):
        s = _SendStub()
        s.send("e", "temperature", 16.0, "set_temperature", {"temperature": 16.0}, T0)
        self.assertTrue(s.send("e", "temperature", 23.0, "set_temperature", {"temperature": 23.0}, T0 + timedelta(seconds=30)))

    def test_retry_with_backoff_if_never_reflected(self):
        s = _SendStub()
        s.send("e", "hvac_mode", "heat", "set_hvac_mode", {"hvac_mode": "heat"}, T0)
        self.assertTrue(s.send("e", "hvac_mode", "heat", "set_hvac_mode", {"hvac_mode": "heat"}, T0 + timedelta(minutes=6)))
        self.assertFalse(s.send("e", "hvac_mode", "heat", "set_hvac_mode", {"hvac_mode": "heat"}, T0 + timedelta(minutes=12)))
        self.assertTrue(s.send("e", "hvac_mode", "heat", "set_hvac_mode", {"hvac_mode": "heat"}, T0 + timedelta(minutes=37)))
        self.assertEqual(len(s.calls), 3)

    def test_kinds_are_independent(self):
        s = _SendStub()
        s.send("e", "hvac_mode", "heat", "set_hvac_mode", {"hvac_mode": "heat"}, T0)
        self.assertTrue(s.send("e", "fan_mode", "auto", "set_fan_mode", {"fan_mode": "auto"}, T0))


if __name__ == "__main__":
    unittest.main()
