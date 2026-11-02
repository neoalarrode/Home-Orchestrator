"""El recorte de "mantener" de una bomba de calor puede bajar hasta 5 C (3-nov:
con el tope de 3 C el aire del dormitorio seguia pasandose y ciclaba)."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402


class _S:
    def __init__(self):
        self.zone = {"name": "Dormitorio"}
        self._hold_trim = {"ac": 3.0}
        self._bump_hold_trim = zr.ZoneRunner._bump_hold_trim.__get__(self)


class TrimCap(unittest.TestCase):
    def test_keeps_lowering_past_three(self):
        s = _S()
        for _ in range(10):
            s._bump_hold_trim("ac", zr.HOLD_TRIM_STEP_DEG, "se pasa enseguida")
        self.assertEqual(s._hold_trim["ac"], 5.0)


if __name__ == "__main__":
    unittest.main()
