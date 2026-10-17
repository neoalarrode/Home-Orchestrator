"""El handle climate de tuya_native expone la oscilación (aspas) para que una
zona de Climate con este AC de delegado ofrezca swing. Antes el handle no tenía
swing_mode/swing_modes/set_swing_mode, así que la entidad de Climate no lo
publicaba aunque el equipo (y su entidad Tuya en HA) sí lo soporta.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from tuya_native.handles import TuyaClimateHandle  # noqa: E402


class _Ctl:
    def __init__(self, state):
        self._state = dict(state)
        self.sets = []

    def get_state(self):
        return dict(self._state)

    def set_dp(self, code, value):
        self.sets.append((code, value))
        self._state[code] = value


def _codes():
    return {
        "switch": {"spec": {}},
        "mode": {"spec": {"range": ["cold", "hot"]}},
        "up_down_sweep": {"spec": {"range": ["0", "1", "2", "3"]}},
    }


class SwingHandle(unittest.TestCase):
    def test_exposes_swing_modes_and_current(self):
        ctl = _Ctl({"switch": True, "mode": "cold", "up_down_sweep": "2"})
        h = TuyaClimateHandle(ctl, _codes())
        self.assertEqual(h.swing_modes, ["0", "1", "2", "3"])
        self.assertEqual(h.swing_mode, "2")

    def test_set_swing_mode_writes_the_dp(self):
        ctl = _Ctl({"switch": True, "up_down_sweep": "0"})
        h = TuyaClimateHandle(ctl, _codes())
        h.set_swing_mode("3")
        self.assertIn(("up_down_sweep", "3"), ctl.sets)

    def test_set_unknown_swing_is_ignored(self):
        ctl = _Ctl({"switch": True, "up_down_sweep": "0"})
        h = TuyaClimateHandle(ctl, _codes())
        h.set_swing_mode("9")  # fuera del rango
        self.assertEqual(ctl.sets, [])

    def test_no_swing_dp_means_no_swing(self):
        ctl = _Ctl({"switch": True, "mode": "cold"})
        h = TuyaClimateHandle(ctl, {"switch": {"spec": {}}, "mode": {"spec": {"range": ["cold"]}}})
        self.assertEqual(h.swing_modes, [])
        self.assertIsNone(h.swing_mode)
        h.set_swing_mode("1")  # no revienta, no escribe nada
        self.assertEqual(ctl.sets, [])


if __name__ == "__main__":
    unittest.main()
