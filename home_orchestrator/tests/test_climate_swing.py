"""Ingesta de oscilacion (swing) en las zonas climate (stdlib unittest).

El componente agrega los swing_modes que exponen sus delegados, los ofrece en
la entidad y propaga la eleccion del usuario al equipo -- sin acoplarse a
ninguna marca (lee via el estado del delegado / getattr, con default vacio).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402


class _Stub:
    """ZoneRunner minimo con los metodos de swing reales enganchados."""
    def __init__(self, delegates, states):
        self.zone_id = "z"
        self.zone = {"climate_entities": list(delegates)}
        self._states = states
        self._manual_swing_mode = None
        self._swing_mode = None
        self._swing_modes = None
        self.service_calls = []
        self._manual_fan_mode = None
        for name in ("_available_swing_modes", "set_swing_mode", "_drive_delegate_fan_mode", "swing_modes"):
            pass
        self._available_swing_modes = zr.ZoneRunner._available_swing_modes.__get__(self)
        self._drive_delegate_fan_mode = zr.ZoneRunner._drive_delegate_fan_mode.__get__(self)
        self.set_swing_mode = zr.ZoneRunner.set_swing_mode.__get__(self)

    # colaboradores que los metodos reales usan
    def _get_state(self, entity_id):
        return self._states.get(entity_id)

    def _call_climate_service(self, entity_id, service, data=None):
        self.service_calls.append((entity_id, service, data))

    def decide_and_act(self):
        self.service_calls.append(("<decide_and_act>", None, None))


def _delegate_state(swing_modes, swing_mode="0", fan_modes=None):
    return {"state": "cool", "attributes": {
        "swing_modes": swing_modes, "swing_mode": swing_mode,
        "fan_modes": fan_modes or [], "fan_mode": None,
    }}


class SwingAggregation(unittest.TestCase):
    def test_union_of_delegate_swings_in_order(self):
        s = _Stub(["tuya:a", "tuya:b"], {
            "tuya:a": _delegate_state(["0", "1", "2"]),
            "tuya:b": _delegate_state(["2", "3"]),
        })
        self.assertEqual(s._available_swing_modes(), ["0", "1", "2", "3"])

    def test_no_delegate_swing_means_empty(self):
        s = _Stub(["tuya:a"], {"tuya:a": _delegate_state([])})
        self.assertEqual(s._available_swing_modes(), [])

    def test_unreadable_delegate_skipped(self):
        s = _Stub(["tuya:a", "tuya:b"], {"tuya:a": None, "tuya:b": _delegate_state(["1"])})
        self.assertEqual(s._available_swing_modes(), ["1"])


class SwingCommand(unittest.TestCase):
    def test_set_swing_rejects_unknown(self):
        s = _Stub(["tuya:a"], {})
        s._swing_modes = ["0", "1", "2", "3"]
        s.set_swing_mode("9")
        self.assertIsNone(s._manual_swing_mode)
        self.assertNotIn(("<decide_and_act>", None, None), s.service_calls)

    def test_set_swing_accepts_and_pushes(self):
        s = _Stub(["tuya:a"], {})
        s._swing_modes = ["0", "1", "2", "3"]
        s.set_swing_mode("2")
        self.assertEqual(s._manual_swing_mode, "2")
        self.assertEqual(s._swing_mode, "2")
        self.assertIn(("<decide_and_act>", None, None), s.service_calls)

    def test_drive_sends_swing_only_when_user_asked_and_supported(self):
        s = _Stub(["tuya:a"], {})
        state = _delegate_state(["0", "1", "2", "3"], swing_mode="0", fan_modes=["auto", "low"])
        # sin peticion del usuario -> no se manda swing
        s._drive_delegate_fan_mode("tuya:a", state, urgent=False)
        self.assertFalse([c for c in s.service_calls if c[1] == "set_swing_mode"])
        # el usuario pide swing "3" -> se manda una vez
        s._manual_swing_mode = "3"
        s._drive_delegate_fan_mode("tuya:a", state, urgent=False)
        self.assertEqual(
            [c for c in s.service_calls if c[1] == "set_swing_mode"],
            [("tuya:a", "set_swing_mode", {"swing_mode": "3"})],
        )

    def test_drive_does_not_resend_if_already_in_desired_swing(self):
        s = _Stub(["tuya:a"], {})
        s._manual_swing_mode = "2"
        state = _delegate_state(["0", "1", "2"], swing_mode="2")
        s._drive_delegate_fan_mode("tuya:a", state, urgent=False)
        self.assertFalse([c for c in s.service_calls if c[1] == "set_swing_mode"])


if __name__ == "__main__":
    unittest.main()
