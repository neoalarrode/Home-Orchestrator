"""Pausas pasajeras de un delegado (ventilar por ventana, dry, off porque el
equipo no admite lo que pide la zona) y su vuelta. Caso real 31-oct: la puerta
del patio se abrio y cerro 4 veces en un minuto, Climate encendio el AC del salon
(que estaba apagado) en ventilador y, al cerrarse, no supo a que volver -- 20 h
con el ventilador. Tambien el margen de la puerta: abrir y cerrar al pasar no
debe pausar nada.
"""
import os, sys, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402

AC_MODES = ["off", "heat", "cool", "dry", "fan_only"]
RAD_MODES = ["off", "heat"]


class _Stub:
    def __init__(self, state, zone_mode="heat_cool", low=21.0, high=25.0):
        self.state = state
        self.hvac_mode = zone_mode
        self.target_temperature_low, self.target_temperature_high = low, high
        self.zone = {"name": "Salón", "min_temp": 16.0, "max_temp": 30.0}
        self.calls = []
        self._deviation_at_rest = set()
        for attr in ("_delegate_last_command", "_delegate_last_active", "_delegate_deviations",
                     "_activation_probe", "_delegate_activation_margin", "_delegate_pre_temp", "_park_since"):
            setattr(self, attr, {})
        for name in ("_drive_climate_actuator", "_drive_climate_idle", "_send_delegate_once",
                     "_note_delegate_command", "_compensate_delegate_target", "_learn_activation_margin",
                     "_fallback_thermal_mode", "_learn_rest_deviation"):
            setattr(self, name, getattr(zr.ZoneRunner, name).__get__(self))

    def _get_state(self, e): return self.state
    def _respect_delegate_override(self, *a): return False
    def _delegate_send_allowed(self, *a): return True
    def _drive_delegate_fan_mode(self, *a, **k): pass

    def _call_climate_service(self, e, service, data=None):
        self.calls.append((service, data))
        # el equipo aplica la orden
        if service == "set_hvac_mode":
            self.state["state"] = data["hvac_mode"]
        elif service == "set_temperature":
            self.state["attributes"]["temperature"] = data["temperature"]


def _st(mode, temp=23.0, modes=AC_MODES):
    return {"state": mode, "attributes": {"hvac_modes": list(modes), "current_temperature": 25.0,
                                          "temperature": temp, "min_temp": 16.0, "max_temp": 30.0}}


class PauseRestore(unittest.TestCase):
    def test_window_on_an_off_ac_returns_it_to_off(self):
        s = _Stub(_st("off"))
        s._drive_climate_actuator("e", "fan_only", 21.0, 22.0, simulate=False)
        self.assertEqual(s.state["state"], "fan_only")
        s.calls.clear()
        s._drive_climate_idle("e", 22.0, 0.3, False)          # se cierra la puerta
        self.assertEqual(s.calls, [("set_hvac_mode", {"hvac_mode": "off"})])

    def test_dry_on_a_heating_ac_returns_to_heat_parked(self):
        s = _Stub(_st("heat"))
        s._drive_climate_actuator("e", "dry", 21.0, 22.0, simulate=False)
        s.calls.clear()
        s._drive_climate_idle("e", 22.0, 0.3, False)          # acaba el dry
        self.assertEqual(s.calls, [("set_hvac_mode", {"hvac_mode": "heat"}),
                                   ("set_temperature", {"temperature": 16.0})])

    def test_radiator_turned_off_for_dry_is_turned_back_on_in_heat(self):
        s = _Stub(_st("heat", temp=22, modes=RAD_MODES))
        s._drive_climate_actuator("e", "dry", 21.0, 22.0, simulate=False)
        self.assertEqual(s.state["state"], "off")             # en dry SI se apaga el radiador
        s.calls.clear()
        s._drive_climate_idle("e", 22.0, 0.3, False)
        self.assertEqual(s.calls[0], ("set_hvac_mode", {"hvac_mode": "heat"}))

    def test_stuck_in_fan_without_record_parks_on_nearest_side(self):
        # El AC real del salon: en ventilador, sin registro; 22.0 con 21/25 -> calor.
        s = _Stub(_st("fan_only", temp=20.0))
        s._drive_climate_idle("e", 22.0, 0.3, False)
        self.assertEqual(s.calls, [("set_hvac_mode", {"hvac_mode": "heat"}),
                                   ("set_temperature", {"temperature": 16.0})])

    def test_nearest_side_cool(self):
        s = _Stub(_st("fan_only", temp=20.0))
        s._drive_climate_idle("e", 24.5, 0.3, False)
        self.assertEqual(s.calls[0], ("set_hvac_mode", {"hvac_mode": "cool"}))

    def test_zone_off_is_not_a_pause(self):
        s = _Stub(_st("heat"), zone_mode="off")
        s._drive_climate_actuator("e", "idle", 21.0, 22.0, simulate=False)
        self.assertEqual(s.state["state"], "off")
        self.assertEqual(s._delegate_pre_temp, {})             # tu off no se deshace solo

    def test_real_demand_forgets_the_pause(self):
        s = _Stub(_st("off"))
        s._drive_climate_actuator("e", "fan_only", 21.0, 22.0, simulate=False)
        s._drive_climate_actuator("e", "heat", 21.0, 20.0, simulate=False)
        self.assertNotIn("e", s._delegate_pre_temp)


class IdleFanBackToAuto(unittest.TestCase):
    def test_parked_ac_fan_returns_to_auto(self):
        st = _st("heat", temp=16.0)
        st["attributes"].update(fan_mode="strong", fan_modes=["auto", "low", "middle", "strong"])
        s = _Stub(st)
        s._manual_fan_mode = None
        s._manual_swing_mode = None
        s._drive_delegate_fan_mode = zr.ZoneRunner._drive_delegate_fan_mode.__get__(s)
        s._delegate_last_active["e"] = ("heat", 21.0)
        s._drive_climate_idle("e", 22.0, 0.3, False)
        self.assertIn(("set_fan_mode", {"fan_mode": "auto"}), s.calls)


class _DoorStub:
    def __init__(self, state):
        self.zone = {zr.CONF_DOOR_WINDOW_ENTITIES: ["binary_sensor.puerta"]}
        self._state = state
        self.open = zr.ZoneRunner._real_door_window_open.__get__(self)

    def _get_state(self, e): return self._state


class DoorDebounce(unittest.TestCase):
    def _now(self): return datetime.now(timezone.utc)

    def test_brief_opening_is_ignored(self):
        st = {"state": "on", "last_changed": (self._now() - timedelta(seconds=20)).isoformat()}
        self.assertFalse(_DoorStub(st).open())

    def test_sustained_opening_pauses(self):
        st = {"state": "on", "last_changed": (self._now() - timedelta(seconds=90)).isoformat()}
        self.assertTrue(_DoorStub(st).open())

    def test_epoch_timestamp_from_compressed_subscription(self):
        st = {"state": "on", "last_changed": (self._now() - timedelta(seconds=20)).timestamp()}
        self.assertFalse(_DoorStub(st).open())

    def test_without_timestamp_behaves_as_before(self):
        self.assertTrue(_DoorStub({"state": "on"}).open())

    def test_closed(self):
        self.assertFalse(_DoorStub({"state": "off", "last_changed": self._now().isoformat()}).open())


if __name__ == "__main__":
    unittest.main()
