"""Seguimiento de carga: Energy hace de contador de cada bateria en modo
semiautomatico (carga base), con margen anti-vertido, limite por bateria y el
tope de potencia contratada."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import load_follower as lf  # noqa: E402

F = [{"id": "ultra", "max_w": 1200, "available_wh": 2600}, {"id": "ef", "max_w": 1200, "available_wh": 900}]


class Targets(unittest.TestCase):
    def test_covers_the_house_minus_margin(self):
        t = lf.compute_targets(1000, {}, F, True, 5000, 50)
        self.assertEqual(sum(t.values()), 950)
        self.assertGreater(t["ultra"], t["ef"])                 # mas energia, mas parte

    def test_starts_from_what_they_already_deliver(self):
        # el Shelly marca 200 W con 800 W ya entregados: la casa pide 1000
        t = lf.compute_targets(200, {"ultra": 600, "ef": 200}, F, True, 5000, 50)
        self.assertEqual(sum(t.values()), 950)

    def test_export_reduces_the_output(self):
        t = lf.compute_targets(-300, {"ultra": 800, "ef": 200}, F, True, 5000, 50)
        self.assertEqual(sum(t.values()), 650)

    def test_never_above_each_battery_limit(self):
        t = lf.compute_targets(4000, {}, F, True, 5000, 50)
        self.assertEqual(t, {"ultra": 1200, "ef": 1200})

    def test_no_discharge_when_planner_says_no(self):
        self.assertEqual(sum(lf.compute_targets(3000, {}, F, False, 5000, 50).values()), 0)

    def test_peak_shaving_above_contracted_power(self):
        t = lf.compute_targets(5600, {}, F, False, 5000, 50)
        self.assertEqual(sum(t.values()), 650)                   # 5600 - (5000 - 50)

    def test_one_battery_empty(self):
        f = [dict(F[0]), dict(F[1], available_wh=0)]
        self.assertEqual(lf.compute_targets(1500, {}, f, True, 5000, 50), {"ultra": 1200, "ef": 0})


class Follower(unittest.TestCase):
    def test_meter_lost_sets_zero(self):
        sent = []
        ctx = {"mode": "on", "meter_host": "x", "followers": F, "discharge_allowed": True,
               "contracted_w": 5000, "margin_w": 50, "batteries_cfg": [{"id": "ultra"}, {"id": "ef"}]}
        f = lf.LoadFollower(lambda: ctx, lambda b, w: sent.append((b["id"], w)) or True)
        f._current = {"ultra": 500, "ef": 200}
        orig = lf.read_shelly_w
        lf.read_shelly_w = lambda *a, **k: None
        try:
            f._last_meter_ts = -100
            f._tick(None)
        finally:
            lf.read_shelly_w = orig
        self.assertEqual(sorted(sent), [("ef", 0.0), ("ultra", 0.0)])

    def test_shadow_sends_nothing(self):
        sent = []
        ctx = {"mode": "shadow", "meter_host": "x", "followers": F, "discharge_allowed": True,
               "contracted_w": 5000, "margin_w": 50, "batteries_cfg": [{"id": "ultra"}, {"id": "ef"}]}
        f = lf.LoadFollower(lambda: ctx, lambda b, w: sent.append(w) or True)
        orig = lf.read_shelly_w
        lf.read_shelly_w = lambda *a, **k: 1000.0
        try:
            f._tick(None)
        finally:
            lf.read_shelly_w = orig
        self.assertEqual(sent, [])
        self.assertEqual(sum(f.status["targets"].values()), 950)


if __name__ == "__main__":
    unittest.main()
