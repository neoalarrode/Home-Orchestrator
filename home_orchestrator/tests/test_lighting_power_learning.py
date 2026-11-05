"""5-nov: el consumo de cada zona de luz se aprende solo, comparando el consumo
de la casa al encender/apagar cada luz, y se autocorrige."""
import os, sys, time, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from lighting import power_learning as pl  # noqa: E402


def _house(base, events):
    """Serie escalonada: base + vatios de cada luz encendida (eventos (ts, w))."""
    pts, level = [(0.0, base)], base
    for ts, w in sorted(events):
        level += w
        pts.append((ts, level))
    return lambda t0, t1: [p for p in pts if p[0] <= t1]


class Learn(unittest.TestCase):
    def setUp(self):
        pl._events.clear()

    def _cycle(self, learner, entity, t, b, watts, house_events):
        learner.observe(t, {entity: b}); house_events.append((t + 2, watts))
        learner.observe(t + 300, {entity: 0.0}); house_events.append((t + 302, -watts))

    def test_learns_watts_and_zone_power(self):
        lr, ev = pl.LightPowerLearner({}), []
        t0 = time.time() - 86400
        lr.observe(t0 - 10, {"light.a": 0.0})
        for k in range(6):
            self._cycle(lr, "light.a", t0 + k * 1000, 1.0, 9.0, ev)
        lr.process_due(time.time(), _house(300.0, ev))
        self.assertEqual(pl.light_power(lr.state["samples"]["light.a"], 1.0), 9.0)
        self.assertEqual(lr.update_power(time.time(), {"light.a": 0.5}), 4.5)
        self.assertEqual(lr.unlearned, [])

    def test_other_light_nearby_is_discarded(self):
        lr, ev = pl.LightPowerLearner({}), []
        t = time.time() - 3600
        lr.observe(t - 10, {"light.a": 0.0})
        lr.observe(t, {"light.a": 1.0}); ev.append((t + 2, 9.0))
        pl._note_event(t + 20, "light.b")                       # otra luz a 20 s
        lr.process_due(time.time(), _house(300.0, ev))
        self.assertNotIn("light.a", lr.state["samples"])

    def test_outlier_ignored_and_window_rolls(self):
        now = time.time()
        samples = [[now - 100 * k, 1.0, 10.0] for k in range(8)] + [[now - 50, 1.0, 250.0]]
        self.assertEqual(pl.light_power(samples, 1.0), 10.0)            # el pico (horno) no cuenta
        old = [[now - 40 * 86400, 1.0, 10.0] for _ in range(8)]
        self.assertIsNone(pl.light_power(old, 1.0))                     # muestras caducadas: reaprende

    def test_brightness_curve_fitted(self):
        now = time.time()
        samples = [[now, b, 2.0 + 8.0 * b] for b in (0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 0.9, 1.0)]
        self.assertAlmostEqual(pl.light_power(samples, 0.5), 6.0, places=1)

    def test_unlearned_is_not_invented(self):
        lr = pl.LightPowerLearner({})
        self.assertEqual(lr.update_power(time.time(), {"light.x": 1.0}), 0.0)
        self.assertEqual(lr.unlearned, ["light.x"])

    def test_energy_integrates(self):
        now = time.time()
        s = {"samples": {"light.a": [[now, 1.0, 10.0]] * 6}}
        lr = pl.LightPowerLearner(s)
        lr.update_power(now, {"light.a": 1.0})
        lr.update_power(now + 360, {"light.a": 0.0})                   # 6 min a 10 W = 1 Wh
        self.assertAlmostEqual(s["energy_wh"], 1.0, places=3)
        self.assertTrue(s["energy_since"])

    def test_median_between(self):
        pts = [(0.0, 100.0), (10.0, 200.0), (12.0, 100.0)]
        self.assertEqual(pl._median_between(pts, 0.0, 20.0), 100.0)


if __name__ == "__main__":
    unittest.main()
