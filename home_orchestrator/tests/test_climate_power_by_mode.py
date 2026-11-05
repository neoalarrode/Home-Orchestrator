"""Potencia de la zona por MODO y segun el EXTERIOR (4-nov): una bomba de calor
gasta distinto calentando que enfriando, y distinto con 0 C que con 12 C fuera."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from climate import power_model as pm  # noqa: E402
from climate import heat_forecast as hf  # noqa: E402


class FitMode(unittest.TestCase):
    def test_line_with_outdoor_spread(self):
        # Mas frio fuera, mas gasta: 1300 W a 0 C, 900 W a 10 C.
        s = [(1300 - 40 * t, float(t)) for t in (0, 2, 4, 6, 8, 10)]
        m = {"by_mode": {"heat": pm._fit_mode(s)}}
        self.assertAlmostEqual(pm.zone_power_at(m, "heat", 0.0), 1300, delta=1)
        self.assertAlmostEqual(pm.zone_power_at(m, "heat", 10.0), 900, delta=1)
        # Fuera de lo visto: como mucho FIT_EXTRAPOLATE_C mas alla.
        self.assertAlmostEqual(pm.zone_power_at(m, "heat", -20.0), 1300 + 40 * pm.FIT_EXTRAPOLATE_C, delta=1)

    def test_heat_and_cool_learned_apart(self):
        m = {"by_mode": {"heat": pm._fit_mode([(1000, None)] * 3), "cool": pm._fit_mode([(600, None)] * 3)}}
        self.assertEqual(pm.zone_power_at(m, "heat", 5.0), 1000)
        self.assertEqual(pm.zone_power_at(m, "cool", 30.0), 600)

    def test_not_enough_samples_or_spread(self):
        self.assertIsNone(pm.zone_power_at({"by_mode": {"cool": pm._fit_mode([(600, 30.0)] * 2)}}, "cool", 30.0))
        flat = pm._fit_mode([(900, 10.0 + i * 0.3) for i in range(8)])     # sin variedad de exterior
        self.assertNotIn("b", flat)
        self.assertEqual(pm.zone_power_at({"by_mode": {"heat": flat}}, "heat", -5.0), 900)
        self.assertIsNone(pm.zone_power_at({}, "heat", 5.0))


class ForecastUsesModePower(unittest.TestCase):
    def test_colder_hours_cost_more_with_a_heat_pump(self):
        power = lambda mode, ext: 1300 - 40 * ext if mode == "heat" else None
        w = hf.project(21.0, [10.0, 0.0], [(21.0, None)] * 2, 1.0, 0.0, 0.05, power)
        self.assertGreater(w[1] / 0.05 / 21, w[0] / 0.05 / 11)   # mas horas de equipo y cada una mas cara

    def test_unknown_power_for_the_needed_mode_means_no_forecast(self):
        power = lambda mode, ext: 900 if mode == "heat" else None
        self.assertIsNone(hf.project(27.0, [32.0] * 2, [(None, 25.0)] * 2, 0.0, 1.0, 0.05, power))


if __name__ == "__main__":
    unittest.main()
