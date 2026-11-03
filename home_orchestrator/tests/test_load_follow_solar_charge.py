"""Carga con excedente de placas en alterna (3-nov): el seguimiento sube el
limite de carga cuando la casa vierte y lo baja cuando importa mientras carga.
La carga por alterna pasa por el Shelly: la casa real es contador + descarga -
carga. El retraso y el ritmo de cada bateria los aprende Energy solo, midiendo
en la propia bateria (no valores fijos)."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import load_follower as lf  # noqa: E402

lf.ASYNC_CALLS = False
_REAL_MONOTONIC = lf.time.monotonic


class Pure(unittest.TestCase):
    def test_house_net_discounts_ac_charging(self):
        # El Shelly marca 300 W importando, pero 500 W son la carga de la bateria.
        self.assertEqual(lf.house_net_w(300.0, {}, {"a": 500.0}), -200.0)

    def test_needs_real_surplus_to_start(self):
        f = [{"id": "a", "headroom_wh": 1000, "max_charge_w": 1050}]
        self.assertEqual(lf.solar_charge_targets(-30.0, f, charging_now=False), {})
        self.assertEqual(lf.solar_charge_targets(-200.0, f, charging_now=False), {"a": 200.0})
        self.assertEqual(lf.solar_charge_targets(-30.0, f, charging_now=True), {"a": 30.0})

    def test_split_by_headroom_and_full_battery_left_out(self):
        f = [{"id": "a", "headroom_wh": 1500, "max_charge_w": 1050},
             {"id": "b", "headroom_wh": 500, "max_charge_w": 1050},
             {"id": "c", "headroom_wh": 0, "max_charge_w": 1050}]
        self.assertEqual(lf.solar_charge_targets(-800.0, f, charging_now=False), {"a": 600.0, "b": 200.0})

    def test_tiny_share_goes_to_the_other(self):
        f = [{"id": "a", "headroom_wh": 1900, "max_charge_w": 1050},
             {"id": "b", "headroom_wh": 100, "max_charge_w": 1050}]
        self.assertEqual(lf.solar_charge_targets(-300.0, f, charging_now=False), {"b": 0.0, "a": 300.0})


class Learner(unittest.TestCase):
    def test_learns_delay_and_ramp_from_the_battery_itself(self):
        saved = {}
        lr = lf.ResponseLearner(store=(lambda: None, lambda d: saved.update(d)))
        # Medida real del Despacho (3-nov): orden 300 -> 600 W en t=0; la red
        # empieza a moverse a 1 s y llega a ~600 a los 5.6 s.
        lr.order("d", "charge", 0.0, 300.0, 600.0)
        for t, w in ((0.5, 302), (1.0, 358), (2.5, 444), (3.6, 533), (4.6, 630)):
            lr.observe("d", "charge", t, w)
        p = lr.get("d", "charge_up")
        self.assertAlmostEqual(p["delay"], 1.0, delta=0.3)
        self.assertGreater(p["ramp"], 40); self.assertLess(p["ramp"], 200)
        self.assertIn("d", saved)                     # se guarda para el reinicio

    def test_small_or_reversed_orders_do_not_teach(self):
        lr = lf.ResponseLearner()
        lr.order("d", "base", 0.0, 300.0, 350.0)       # salto pequeño: no se mide
        lr.observe("d", "base", 2.0, 350.0)
        lr._probes.clear()
        self.assertEqual(lr.get("d", "base_up"), lf.ResponseLearner.DEFAULTS["base_up"])
        lr.order("d", "base", 0.0, 300.0, 800.0)
        lr.order("d", "base", 0.5, 800.0, 200.0)      # se da la vuelta: medida anulada
        lr.observe("d", "base", 3.0, 800.0)
        self.assertEqual(lr.get("d", "base_up"), lf.ResponseLearner.DEFAULTS["base_up"])

    def test_out_of_range_values_are_clamped(self):
        lr = lf.ResponseLearner()
        lr.order("d", "charge", 0.0, 0.0, 500.0)
        lr.observe("d", "charge", 30.0, 500.0)         # pasado el tiempo maximo: se descarta
        self.assertEqual(lr.get("d", "charge_up"), lf.ResponseLearner.DEFAULTS["charge_up"])


class _Plant:
    """Casa + placas en alterna + STREAM: al subir se nota a `up_delay` s y sube
    `ramp` W/s; al bajar tarda `down_delay` s y cae de golpe (como el 3-nov)."""
    def __init__(self, house_w, up_delay=1.0, ramp=100.0, down_delay=2.5):
        self.house_w, self.pv_w, self.t = house_w, 0.0, 0.0
        self.up_delay, self.ramp, self.down_delay = up_delay, ramp, down_delay
        self.charge_act, self.base_act = {}, {}
        self.cmd_log = []          # (t, id, "charge"/"base", w)

    def step(self, dt=1.0):
        self.t += dt
        for kind, act in (("charge", self.charge_act), ("base", self.base_act)):
            for bid in {c[1] for c in self.cmd_log if c[2] == kind}:
                cmds = [c for c in self.cmd_log if c[1] == bid and c[2] == kind]
                up = [w for (t, _, _, w) in cmds if t <= self.t - self.up_delay]
                down = [w for (t, _, _, w) in cmds if t <= self.t - self.down_delay]
                cur = act.get(bid, 0.0)
                if up and up[-1] > cur:
                    cur = min(up[-1], cur + self.ramp * dt)
                elif down and down[-1] < cur:
                    cur = down[-1]
                act[bid] = cur

    def grid_w(self, bid):
        """Puerto de red de la unidad: + entrega, - toma de red."""
        return self.base_act.get(bid, 0.0) - self.charge_act.get(bid, 0.0)

    def meter(self):
        return self.house_w - self.pv_w - sum(self.base_act.values()) + sum(self.charge_act.values())


class Simulation(unittest.TestCase):
    def _run(self, plant, seconds, ctx_extra=None, pv_profile=None, learner=None):
        followers = [{"id": "a", "max_w": 1200, "available_wh": 500, "soc": 50, "headroom_wh": 900, "max_charge_w": 1050},
                     {"id": "b", "max_w": 1200, "available_wh": 500, "soc": 50, "headroom_wh": 900, "max_charge_w": 1050}]
        ctx = {"mode": "on", "meter_host": "x", "followers": followers, "discharge_allowed": False,
               "contracted_w": 5000, "margin_w": 0, "deadband_w": 0, "solar_charge": True, "grid_charge_now": False,
               "batteries_cfg": [{"id": "a", "name": "a"}, {"id": "b", "name": "b"}]}
        ctx.update(ctx_extra or {})

        def context():
            for f in followers:
                f["grid_w"] = plant.grid_w(f["id"])
                f["outlets_w"] = 0.0
            return ctx

        def send(b, w):
            plant.cmd_log.append((plant.t, b["id"], "base", w)); return True

        def charge(b, w):
            plant.cmd_log.append((plant.t, b["id"], "charge", w)); return True
        f = lf.LoadFollower(context, send, charge=charge, learner=learner)
        orig = lf.read_shelly_w
        meters = []
        try:
            lf.time.monotonic = lambda: plant.t
            for s in range(seconds):
                if pv_profile:
                    plant.pv_w = pv_profile(s)
                lf.read_shelly_w = lambda *a, **k: round(plant.meter()) + (0.1 if s % 2 else 0.0)
                f._tick(None)
                plant.step()
                meters.append(plant.meter())
        finally:
            lf.read_shelly_w = orig
            lf.time.monotonic = _REAL_MONOTONIC
        return meters, plant, f

    def test_absorbs_surplus_and_follows_a_cloud(self):
        # Casa 400 W; placas 1400 W, nube a los 60 s (300 W), vuelve a los 90 s.
        pv = lambda s: 1400.0 if s < 60 or s >= 90 else 300.0
        meters, plant, f = self._run(_Plant(400.0), 130, pv_profile=pv)
        self.assertLess(meters[0], -700)                        # empieza vertiendo (1000 W)
        self.assertTrue(all(abs(m) < 80 for m in meters[30:60]), meters[30:60])
        # Con nube la casa (400) pide mas que el sol (300): esos 100 W vienen de red,
        # pero NADA para cargar -- la carga baja a 0 en cuanto el equipo lo aplica.
        self.assertTrue(all(m <= 100 + 5 for m in meters[60 + 4:90]), meters[60:90])
        self.assertTrue(all(abs(m) < 80 for m in meters[120:]), meters[120:])
        # Nunca carga y descarga la misma bateria a la vez.
        self.assertFalse(any(plant.base_act.get(b, 0) > 0 and plant.charge_act.get(b, 0) > 0 for b in "ab"))

    def test_learns_the_real_battery_starting_from_wrong_values(self):
        # La bateria simulada es mas lenta que los valores de partida.
        lr = lf.ResponseLearner()
        pv = lambda s: 1400.0 if (s // 40) % 2 == 0 else 300.0
        self._run(_Plant(400.0, up_delay=2.0, ramp=60.0, down_delay=3.0), 1200, pv_profile=pv, learner=lr)
        learned = [lr.get(b, "charge_up") for b in "ab" if "charge_up" in (lr.params.get(b) or {})]
        self.assertTrue(learned, lr.params)
        for p in learned:
            self.assertAlmostEqual(p["delay"], 2.0, delta=1.3)
            self.assertLess(p["ramp"], 90)                     # aprende que sube mas despacio de lo que creia

    def test_discovers_a_faster_battery(self):
        lr = lf.ResponseLearner()
        pv = lambda s: 1400.0 if (s // 40) % 2 == 0 else 300.0
        self._run(_Plant(400.0, up_delay=1.0, ramp=250.0, down_delay=2.0), 1200, pv_profile=pv, learner=lr)
        self.assertGreater(lr.get("a", "charge_up")["ramp"], 150)

    def test_planned_grid_charging_is_left_to_the_plan(self):
        meters, plant, f = self._run(_Plant(400.0), 20, ctx_extra={"grid_charge_now": True},
                                     pv_profile=lambda s: 1400.0)
        self.assertEqual([c for c in plant.cmd_log if c[2] == "charge"], [])

    def test_disabled_by_setting(self):
        meters, plant, f = self._run(_Plant(400.0), 20, ctx_extra={"solar_charge": False},
                                     pv_profile=lambda s: 1400.0)
        self.assertEqual([c for c in plant.cmd_log if c[2] == "charge"], [])

    def test_export_while_discharging_is_not_solar(self):
        # 3-nov 08:22: el relevo de unidades provoco -350 W de vertido durante
        # la descarga y se puso a cargar. Eso es exceso de descarga, no sol.
        followers = [{"id": "a", "max_w": 1200, "available_wh": 500, "soc": 50, "headroom_wh": 900, "max_charge_w": 1050}]
        ctx = {"mode": "on", "followers": followers, "discharge_allowed": True, "contracted_w": 5000,
               "margin_w": 0, "solar_charge": True, "grid_charge_now": False, "batteries_cfg": [{"id": "a"}]}
        charged = []
        f = lf.LoadFollower(lambda: ctx, lambda b, w: True, charge=lambda b, w: charged.append(w) or True)
        f._current = {"a": 300.0}; f._estimate = {"a": 300.0}
        self.assertEqual(f._solar_charge_targets(-350.0, {"a": 300.0}, {}, followers, ctx), {})
        self.assertEqual(f._solar_charge_targets(-350.0, {"a": 0.0}, {}, followers, ctx), {})   # aun no sostenido

    def test_small_export_does_not_start_charging(self):
        meters, plant, f = self._run(_Plant(400.0), 20, pv_profile=lambda s: 430.0)
        self.assertEqual([c for c in plant.cmd_log if c[2] == "charge"], [])


if __name__ == "__main__":
    unittest.main()
