"""5-nov: el consumo de cada zona de luz se aprende solo, comparando el consumo
de la casa al encender/apagar sus luces (tambien en grupo), y se autocorrige."""
import os, sys, time, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from lighting import power_learning as pl  # noqa: E402


def _house(events, base=300.0):
    pts, level = [(0.0, base)], base
    for ts, dw in sorted(events):
        level += dw
        pts.append((ts, level))
    return lambda t0, t1: [p for p in pts if p[0] <= t1]


W = {"light.a": 9.0, "light.b": 12.0, "light.c": 6.0}


class Learn(unittest.TestCase):
    def setUp(self):
        pl._events.clear()

    def _run(self, lr, combos, t0):
        ev = []
        lr.observe(t0 - 10, {e: 0.0 for e in W})
        t = t0
        for combo in combos:
            lr.observe(t, {e: (1.0 if e in combo else 0.0) for e in W}); ev.append((t + 2, sum(W[e] for e in combo)))
            lr.observe(t + 400, {e: 0.0 for e in W}); ev.append((t + 402, -sum(W[e] for e in combo)))
            t += 1000
        lr.process_due(time.time(), _house(ev))

    def test_lights_switched_together_still_learn(self):
        lr = pl.LightPowerLearner({})
        combos = [{"light.a", "light.b", "light.c"}, {"light.a", "light.b"}, {"light.b", "light.c"},
                  {"light.a", "light.c"}, {"light.a"}, {"light.b"}, {"light.c"}] * 4
        self._run(lr, combos, time.time() - 86400)
        for e, w in W.items():
            self.assertAlmostEqual(lr.learned()[e], w, delta=0.2)
        # Solo datos al 100 %: la luz al 50 % se estima con ~20 % fijo + 80 % proporcional.
        self.assertAlmostEqual(lr.update_power(time.time(), {"light.a": 1.0, "light.b": 0.5, "light.c": 0.0}), 16.2, delta=0.4)

    def test_always_together_gives_right_zone_total(self):
        lr = pl.LightPowerLearner({})
        self._run(lr, [{"light.a", "light.b", "light.c"}] * 8, time.time() - 86400)
        self.assertAlmostEqual(lr.update_power(time.time(), {e: 1.0 for e in W}), sum(W.values()), delta=0.3)

    def test_other_zone_light_nearby_is_discarded(self):
        lr = pl.LightPowerLearner({})
        t = time.time() - 3600
        lr.observe(t - 10, {"light.a": 0.0})
        lr.observe(t, {"light.a": 1.0})
        pl._note_event(t + 20, "light.otra_zona")
        lr.process_due(time.time(), _house([(t + 2, 9.0)]))
        self.assertEqual(lr.state["groups"], [])

    def test_outlier_and_aging(self):
        now = time.time()
        groups = [[now - 100 * k, {"light.a": 1.0}, 10.0] for k in range(20)] + [[now - 50, {"light.a": 1.0}, 250.0]]
        self.assertAlmostEqual(sum(pl.solve_lights(groups)["light.a"]), 10.0, delta=0.2)
        old = [[now - 40 * 86400, {"light.a": 1.0}, 10.0] for _ in range(20)]
        self.assertEqual(pl.solve_lights(old), {})

    def test_unlearned_is_not_invented(self):
        lr = pl.LightPowerLearner({})
        self.assertEqual(lr.update_power(time.time(), {"light.x": 1.0}), 0.0)
        self.assertEqual(lr.unlearned, ["light.x"])

    def test_energy_integrates(self):
        now = time.time()
        s = {"groups": [[now, {"light.a": 1.0}, 10.0]] * 20}
        lr = pl.LightPowerLearner(s)
        lr.update_power(now, {"light.a": 1.0})
        lr.update_power(now + 360, {"light.a": 0.0})
        self.assertAlmostEqual(s["energy_wh"], 1.0, delta=0.03)
        self.assertTrue(s["energy_since"])

    def test_old_format_dropped(self):
        s = {"samples": {"light.a": [[1, 1.0, 9.0]]}}
        pl.LightPowerLearner(s)
        self.assertNotIn("samples", s); self.assertEqual(s["groups"], [])

    def test_noisy_house_still_learns_over_time(self):
        # Casa con +-40 W de ruido segundo a segundo y un aire que va y viene:
        # con muchos encendidos/apagados la luz de 9 W sale bien.
        import random
        rng = random.Random(2)
        lr = pl.LightPowerLearner({})
        t0 = time.time() - 5 * 86400
        lr.observe(t0 - 10, {"light.a": 0.0})
        pts, t = [], t0 - 120
        state = 0.0
        events = []
        for k in range(60):
            events.append((t0 + k * 400, 9.0)); events.append((t0 + k * 400 + 200, -9.0))
        ev = sorted(events)
        while t < t0 + 60 * 400 + 100:
            while ev and ev[0][0] + 2 <= t:
                state += ev.pop(0)[1]
            pts.append((t, 700 + 200 * (((t // 900) % 2)) + rng.gauss(0, 40) + state))
            t += 1.0
        for k in range(60):
            lr.observe(t0 + k * 400, {"light.a": 1.0}); lr.observe(t0 + k * 400 + 200, {"light.a": 0.0})
        lr.process_due(time.time(), lambda a, b: [p for p in pts if a - 5 <= p[0] <= b])
        self.assertAlmostEqual(lr.learned()["light.a"], 9.0, delta=3.0)

    def test_always_together_split_evenly(self):
        # Cocina 6-nov: 4 barras + plafon siempre juntas -> reparto a partes
        # iguales, no todo en una.
        now = time.time()
        lights = ["light.b1", "light.b2", "light.b3", "light.b4", "light.p"]
        groups = [[now - 60 * k, {e: 1.0 for e in lights}, 22.0] for k in range(20)]
        got = pl.solve_lights(groups)
        for e in lights:
            self.assertAlmostEqual(got[e][0] + got[e][1], 4.4, delta=0.2)

    def test_night_dim_learns_fixed_part(self):
        # De noche al 1 %: un LED al 1 % gasta ~6 W, no el 1 % de 20 W.
        now = time.time()
        groups = ([[now - 60 * k, {"light.a": 0.01}, 6.0] for k in range(20)]
                  + [[now - 60 * k - 30, {"light.a": 1.0}, 20.0] for k in range(20)])
        lr = pl.LightPowerLearner({"groups": groups})
        self.assertAlmostEqual(lr.update_power(now, {"light.a": 0.01}), 6.0, delta=0.5)
        self.assertAlmostEqual(lr.update_power(now, {"light.a": 1.0}), 20.0, delta=0.5)

    def test_level_during_measurement_is_used(self):
        # Se enciende al 100 % y la zona la baja al 1 % al segundo: el salto
        # medido es del 1 %.
        lr = pl.LightPowerLearner({})
        t = time.time() - 3600
        lr.observe(t - 10, {"light.a": 0.0})
        lr.observe(t, {"light.a": 1.0})
        lr.observe(t + 1, {"light.a": 0.01})
        lr.process_due(time.time(), _house([(t + 2, 6.0)]))
        self.assertEqual(lr.state["groups"][-1][1], {"light.a": 0.01})

    def test_level_change_inside_window_discards(self):
        lr = pl.LightPowerLearner({})
        t = time.time() - 3600
        lr.observe(t - 10, {"light.a": 0.0})
        lr.observe(t, {"light.a": 1.0})
        lr.observe(t + 12, {"light.a": 0.3})
        lr.process_due(time.time(), _house([(t + 2, 6.0)]))
        self.assertEqual(lr.state["groups"], [])

    def test_off_then_back_on_discards(self):
        lr = pl.LightPowerLearner({})
        t = time.time() - 3600
        lr.observe(t - 10, {"light.a": 0.8})
        lr.observe(t, {"light.a": 0.0})
        lr.observe(t + 2, {"light.a": 0.8})
        lr.process_due(time.time(), _house([]))
        self.assertEqual([g for g in lr.state["groups"] if g[1].get("light.a", 0) < 0], [])

    def test_median_between(self):
        self.assertEqual(pl._median_between([(0.0, 100.0), (10.0, 200.0), (12.0, 100.0)], 0.0, 20.0), 100.0)


if __name__ == "__main__":
    unittest.main()
