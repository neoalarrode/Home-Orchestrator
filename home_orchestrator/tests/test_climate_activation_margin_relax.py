"""El margen de arranque de un equipo tambien baja cuando arranca bien (5-nov:
solo subia; el radiador del cuarto de juegos estaba clavado en +3 C)."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402


class _S:
    def __init__(self, margin):
        self.zone = {"name": "Cuarto de Juegos"}
        self._delegate_activation_margin = {"r": margin}
        self._activation_ok_streak = {}
        self._relax_activation_margin = zr.ZoneRunner._relax_activation_margin.__get__(self)


class Relax(unittest.TestCase):
    def test_steps_down_after_consecutive_good_starts(self):
        s = _S(3.0)
        m = 3.0
        for _ in range(zr.ACTIVATION_RELAX_STREAK - 1):
            m = s._relax_activation_margin("r", m, {})
        self.assertEqual(m, 3.0)                              # aun no
        m = s._relax_activation_margin("r", m, {})
        self.assertEqual(m, 3.0 - zr.ACTIVATION_MARGIN_STEP_DEG)
        self.assertEqual(s._delegate_activation_margin["r"], m)

    def test_never_below_zero_and_respects_device_step(self):
        s = _S(0.5)
        m = 0.5
        for _ in range(zr.ACTIVATION_RELAX_STREAK * 3):
            m = s._relax_activation_margin("r", m, {"target_temp_step": 1.0})
        self.assertEqual(m, 0.0)


if __name__ == "__main__":
    unittest.main()
