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


U4 = [{"id": "ultra", "max_w": 1200, "available_wh": 2600, "soc": 80},
      {"id": "ef", "max_w": 1200, "available_wh": 700, "soc": 50},
      {"id": "desp", "max_w": 1200, "available_wh": 900, "soc": 58},
      {"id": "patio", "max_w": 1200, "available_wh": 800, "soc": 55}]


class Efficiency(unittest.TestCase):
    def test_low_demand_uses_one_unit_the_fullest(self):
        self.assertEqual(lf.choose_active(500, U4, []), ["ultra"])

    def test_adds_a_unit_above_900_per_active(self):
        self.assertEqual(lf.choose_active(1000, U4, ["ultra"]), ["ultra", "desp"])

    def test_hysteresis_keeps_two_until_below_600(self):
        self.assertEqual(len(lf.choose_active(800, U4, ["ultra", "desp"])), 2)
        self.assertEqual(lf.choose_active(550, U4, ["ultra", "desp"]), ["ultra"])

    def test_high_demand_uses_all_units(self):
        self.assertEqual(len(lf.choose_active(3000, U4, [])), 4)    # 3000 > 3 x 900
        self.assertEqual(len(lf.choose_active(2500, U4, [])), 3)    # 2500 > 2 x 900

    def test_empty_unit_leaves(self):
        f = [dict(U4[0], available_wh=0)] + U4[1:]
        self.assertEqual(lf.choose_active(500, f, ["ultra"]), ["desp"])

    def test_inactive_units_get_zero(self):
        t = lf.compute_targets(600, {}, U4, True, 5000, 50, active=["ultra"])
        self.assertEqual(t, {"ultra": 550, "ef": 0.0, "desp": 0.0, "patio": 0.0})

    def test_swap_when_another_has_much_more_soc(self):
        f = [dict(U4[0], soc=45), U4[1], U4[2], U4[3]]      # ultra ya mas baja
        self.assertEqual(lf.maybe_swap(["ultra"], f), ["desp"])
        self.assertEqual(lf.maybe_swap(["desp"], U4), ["ultra"])     # 80 vs 58
        self.assertEqual(lf.maybe_swap(["patio"], [dict(x, soc=60) for x in U4]), ["patio"])


GRP = {"id": "grupo:D", "max_w": 1200, "available_wh": 1500, "soc": 55, "output_w": 0.0}


class WithMeterGroups(unittest.TestCase):
    """Una bateria suelta (la sigue Energy) y un grupo con contador propio."""
    ULTRA = [{"id": "ultra", "max_w": 1200, "available_wh": 2600, "soc": 80}]

    def test_small_load_ultra_alone(self):
        act = lf.choose_active(600, self.ULTRA + [GRP], [])
        self.assertEqual(act, ["ultra"])
        t = lf.compute_targets(650, {}, self.ULTRA, True, 5000, 50, others=[GRP], active=act)
        self.assertEqual(t, {"ultra": 600})

    def test_big_load_shared_with_the_group(self):
        # casa 2000 W: el Shelly marca 2000 sin nadie descargando
        act = lf.choose_active(1950, self.ULTRA + [GRP], ["ultra"])
        self.assertEqual(set(act), {"ultra", "grupo:D"})
        # por energia disponible (2600 frente a 1500 Wh): la Ultra X llega a su
        # tope y el grupo cubre el resto (750 W) solo, siguiendo al Shelly
        self.assertEqual(lf.compute_targets(2000, {}, self.ULTRA, True, 5000, 50, others=[GRP], active=act), {"ultra": 1200})
        g = dict(GRP, available_wh=2600)
        t = lf.compute_targets(2000, {}, self.ULTRA, True, 5000, 50, others=[g], active=act)
        self.assertEqual(t, {"ultra": 975})                 # mitad y mitad de 1950

    def test_target_does_not_depend_on_how_the_group_reacted(self):
        # misma casa (2000 W), el grupo ya entrega 700: el Shelly marca menos
        g = dict(GRP, output_w=700)
        a = lf.compute_targets(2000, {}, self.ULTRA, True, 5000, 50, others=[GRP], active=["ultra", "grupo:D"])
        b = lf.compute_targets(1300, {}, self.ULTRA, True, 5000, 50, others=[g], active=["ultra", "grupo:D"])
        self.assertEqual(a, b)

    def test_total_cap_is_both_groups(self):
        self.assertEqual(lf.wanted_total(5000, {}, self.ULTRA, True, 9999, 50, others=[GRP]), 2400)

    def test_empty_group_leaves_it_to_the_ultra(self):
        g = dict(GRP, available_wh=0)
        t = lf.compute_targets(1500, {}, self.ULTRA, True, 5000, 50, others=[g], active=["ultra"])
        self.assertEqual(t, {"ultra": 1200})


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

    def test_leaving_real_mode_releases_the_battery(self):
        released = []
        ctx = {"mode": "on", "meter_host": "x", "followers": F, "discharge_allowed": True,
               "contracted_w": 5000, "margin_w": 50, "batteries_cfg": [{"id": "ultra"}, {"id": "ef"}]}
        f = lf.LoadFollower(lambda: ctx, lambda b, w: True, lambda b: released.append(b["id"]) or True)
        orig = lf.read_shelly_w
        lf.read_shelly_w = lambda *a, **k: 1000.0
        try:
            f._tick(None)                       # sigue y manda
            ctx["mode"] = "shadow"              # vence la prueba
            lf.read_shelly_w = lambda *a, **k: 1001.0
            f._tick(None)
        finally:
            lf.read_shelly_w = orig
        self.assertEqual(sorted(released), ["ef", "ultra"])

    def test_first_time_sends_even_zero(self):
        sent = []
        ctx = {"mode": "on", "meter_host": "x", "followers": F, "discharge_allowed": False,
               "contracted_w": 5000, "margin_w": 50, "batteries_cfg": [{"id": "ultra"}, {"id": "ef"}]}
        f = lf.LoadFollower(lambda: ctx, lambda b, w: sent.append((b["id"], w)) or True)
        orig = lf.read_shelly_w
        lf.read_shelly_w = lambda *a, **k: 800.0
        try:
            f._tick(None)
            lf.read_shelly_w = lambda *a, **k: 810.0
            f._tick(None)                         # ya conocido y sigue en 0: nada
        finally:
            lf.read_shelly_w = orig
        self.assertEqual(sorted(sent), [("ef", 0.0), ("ultra", 0.0)])

    def test_estimate_waits_for_the_ramp(self):
        f = lf.LoadFollower(lambda: {}, lambda b, w: True)
        f._orders["u"] = [(100.0, 1200.0)]
        for t in (100.0, 101.0): f._advance_estimates(t)
        self.assertEqual(f._estimate.get("u", 0.0), 0.0)          # aun sin respuesta
        f._advance_estimates(102.0); self.assertEqual(f._estimate["u"], 300.0)
        f._advance_estimates(103.0); self.assertEqual(f._estimate["u"], 600.0)
        for t in range(104, 110): f._advance_estimates(float(t))
        self.assertEqual(f._estimate["u"], 1200.0)
        f._orders["u"].append((120.0, 200.0))
        f._advance_estimates(122.0); self.assertEqual(f._estimate["u"], 200.0)   # bajada de golpe

    def test_orders_every_second_do_not_freeze_the_estimate(self):
        f = lf.LoadFollower(lambda: {}, lambda b, w: True)
        f._orders["u"] = []
        for t in range(100, 110):
            f._orders["u"].append((float(t), 600.0 + (t % 2) * 30))   # cambia cada segundo
            f._advance_estimates(float(t))
        self.assertGreater(f._estimate["u"], 500.0)               # sigue a lo pedido

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
        # subida limitada: no salta de golpe a los 950 W
        self.assertLessEqual(max(f.status["targets"].values()), lf.MAX_STEP_UP_W)
        # ...pero llega en unas pocas lecturas (misma casa: 1000 W)
        for i in range(6):
            lf.read_shelly_w = lambda *a, **k: 1000.0 - sum(f._current.values()) + 0.1 * (i + 1)
            f._tick(None)
        lf.read_shelly_w = orig
        self.assertAlmostEqual(sum(f.status["targets"].values()), 950, delta=2)


if __name__ == "__main__":
    unittest.main()
