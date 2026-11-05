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
                  {"light.a", "light.c"}, {"light.a"}, {"light.b"}, {"light.c"}] * 2
        self._run(lr, combos, time.time() - 86400)
        for e, w in W.items():
            self.assertAlmostEqual(lr.learned()[e], w, delta=0.2)
        self.assertAlmostEqual(lr.update_power(time.time(), {"light.a": 1.0, "light.b": 0.5, "light.c": 0.0}), 15.0, delta=0.3)

    def test_always_together_gives_right_zone_total(self):
        lr = pl.LightPowerLearner({})
        self._run(lr, [{"light.a", "light.b", "light.c"}] * 6, time.time() - 86400)
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
        groups = [[now - 100 * k, {"light.a": 1.0}, 10.0] for k in range(8)] + [[now - 50, {"light.a": 1.0}, 250.0]]
        self.assertAlmostEqual(pl.solve_lights(groups)["light.a"], 10.0, delta=0.1)
        old = [[now - 40 * 86400, {"light.a": 1.0}, 10.0] for _ in range(8)]
        self.assertEqual(pl.solve_lights(old), {})

    def test_unlearned_is_not_invented(self):
        lr = pl.LightPowerLearner({})
        self.assertEqual(lr.update_power(time.time(), {"light.x": 1.0}), 0.0)
        self.assertEqual(lr.unlearned, ["light.x"])

    def test_energy_integrates(self):
        now = time.time()
        s = {"groups": [[now, {"light.a": 1.0}, 10.0]] * 6}
        lr = pl.LightPowerLearner(s)
        lr.update_power(now, {"light.a": 1.0})
        lr.update_power(now + 360, {"light.a": 0.0})
        self.assertAlmostEqual(s["energy_wh"], 1.0, places=2)
        self.assertTrue(s["energy_since"])

    def test_old_format_dropped(self):
        s = {"samples": {"light.a": [[1, 1.0, 9.0]]}}
        pl.LightPowerLearner(s)
        self.assertNotIn("samples", s); self.assertEqual(s["groups"], [])

    def test_unstable_house_is_discarded(self):
        lr = pl.LightPowerLearner({})
        t = time.time() - 3600
        lr.observe(t - 10, {"light.a": 0.0})
        lr.observe(t, {"light.a": 1.0})
        ev = [(t + 2, 9.0)] + [(t - 30 + 4 * k, (200.0 if k % 2 else -200.0)) for k in range(6)]
        lr.process_due(time.time(), _house(ev))
        self.assertEqual(lr.state["groups"], [])

    def test_median_between(self):
        self.assertEqual(pl._median_between([(0.0, 100.0), (10.0, 200.0), (12.0, 100.0)], 0.0, 20.0), 100.0)


if __name__ == "__main__":
    unittest.main()
