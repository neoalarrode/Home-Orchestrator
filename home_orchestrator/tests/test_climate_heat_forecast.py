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

    def test_cooling_needed_but_rate_not_learned_means_no_forecast(self):
        # Primer dia de aire en verano: sin ritmo de enfriado aprendido.
        self.assertIsNone(hf.project(27.0, [32.0] * 3, [(None, 25.0)] * 3, 1.0, 0.0, 0.05, 1200.0))
        # Justo en la consigna con calor fuera: tambien hace falta saberlo.
        self.assertIsNone(hf.project(25.0, [32.0], [(None, 25.0)], 1.0, 0.0, 0.05, 1200.0))
        # Si no hace falta enfriar, no importa que no lo sepa.
        self.assertEqual(hf.project(22.0, [20.0] * 2, [(None, 25.0)] * 2, 1.0, 0.0, 0.05, 1200.0), [0.0, 0.0])

    def test_no_model_or_power_means_no_forecast(self):
        self.assertIsNone(hf.project(20.0, [5.0], [(21.0, None)], 1.0, 0.0, None, 900.0))
        self.assertIsNone(hf.project(20.0, [5.0], [(21.0, None)], 1.0, 0.0, 0.05, None))
        self.assertIsNone(hf.project(None, [5.0], [(21.0, None)], 1.0, 0.0, 0.05, 900.0))



class Calibration(unittest.TestCase):
    def _hours(self, cal, n, pred, real_w):
        for h in range(n):
            key = f"2026-11-05T{h % 24:02d}:00:00+01:00" if h < 24 else f"2026-11-06T{h - 24:02d}:00:00+01:00"
            cal.note_forecast(key, pred)
            t0 = 1_000_000 + h * 3600
            cal.add_energy(key, t0, real_w)
            for m in range(1, 7):                       # cada 10 min
                cal.add_energy(key, t0 + m * 600, real_w)
        cal.close_hours("2026-11-07T00:00:00+01:00")

    def test_learns_that_the_raw_forecast_triples_reality(self):
        # 4-nov: previsión cruda ~3x lo real (inverter modulando en la consigna).
        cal = hf.Calibrator()
        self._hours(cal, 30, pred=900.0, real_w=300.0)
        self.assertAlmostEqual(cal.factor(), 1 / 3, delta=0.03)

    def test_no_factor_until_a_day_of_comparison(self):
        cal = hf.Calibrator()
        self._hours(cal, 10, pred=900.0, real_w=300.0)
        self.assertIsNone(cal.factor())

    def test_zone_that_barely_heats_keeps_factor_one(self):
        cal = hf.Calibrator()
        self._hours(cal, 30, pred=20.0, real_w=35.0)
        self.assertEqual(cal.factor(), 1.0)

    def test_survives_a_restart(self):
        cal = hf.Calibrator()
        self._hours(cal, 30, pred=900.0, real_w=300.0)
        again = hf.Calibrator(cal.to_state())
        self.assertAlmostEqual(again.factor(), cal.factor(), places=3)


if __name__ == "__main__":
    unittest.main()
