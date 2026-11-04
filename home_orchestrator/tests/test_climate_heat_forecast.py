"""Previsión de vatios de climatizacion por hora para Energy (4-nov)."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from climate import heat_forecast as hf  # noqa: E402


class Project(unittest.TestCase):
    def test_reach_then_hold(self):
        # 19 C, consigna 21, sube 1 C/h a plena potencia (900 W), pierde 5 %/h
        # hacia un exterior de 5 C: 2 h a tope y luego mantener.
        w = hf.project(19.0, [5.0] * 4, [(21.0, None)] * 4, 1.0, 0.0, 0.05, 900.0)
        self.assertEqual(w[:2], [900.0, 900.0])
        hold = 0.05 * (21 - 5) / 1.0 * 900          # 0,8 de la hora -> 720 W
        self.assertAlmostEqual(w[2], hold, delta=1)
        self.assertAlmostEqual(w[3], hold, delta=1)

    def test_colder_outside_means_more_power(self):
        mild = hf.project(21.0, [12.0] * 3, [(21.0, None)] * 3, 2.0, 0.0, 0.04, 1000.0)
        cold = hf.project(21.0, [0.0] * 3, [(21.0, None)] * 3, 2.0, 0.0, 0.04, 1000.0)
        self.assertGreater(sum(cold), sum(mild) * 2)

    def test_lower_setpoint_at_night_saves_until_it_drops(self):
        # Consigna nocturna 18 con la casa a 21: no calienta hasta que baja.
        w = hf.project(21.0, [5.0] * 6, [(18.0, None)] * 6, 1.0, 0.0, 0.05, 900.0)
        self.assertEqual(w[0], 0.0)
        self.assertGreater(w[-1], 0.0)

    def test_cooling_symmetric(self):
        w = hf.project(27.0, [32.0] * 3, [(None, 25.0)] * 3, 0.0, 1.0, 0.05, 1200.0)
        self.assertEqual(w[:2], [1200.0, 1200.0])
        self.assertAlmostEqual(w[2], 0.05 * (32 - 25) / 1.0 * 1200, delta=1)

    def test_no_model_or_power_means_no_forecast(self):
        self.assertIsNone(hf.project(20.0, [5.0], [(21.0, None)], 1.0, 0.0, None, 900.0))
        self.assertIsNone(hf.project(20.0, [5.0], [(21.0, None)], 1.0, 0.0, 0.05, None))
        self.assertIsNone(hf.project(None, [5.0], [(21.0, None)], 1.0, 0.0, 0.05, 900.0))


if __name__ == "__main__":
    unittest.main()
