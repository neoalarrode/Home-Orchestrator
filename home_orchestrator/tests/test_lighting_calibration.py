"""6-nov: calibracion forzada del consumo de una zona de luz DENTRO de Lighting,
por saltos: no hace falta que la casa este quieta (aire modulando, baterias,
otros aparatos que arrancan) ni que las luces respondan al instante."""
import math, os, random, sys, time, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import lighting.zone_runner as zr  # noqa: E402
from lighting import power_learning as pl  # noqa: E402

TRUE_W = {"light.barra": 4.0, "light.plafon": 12.0}


class _Clock:
    def __init__(self): self.t = time.time()
    def now(self): return self.t
    def sleep(self, s): self.t += s


class _Zone:
    """Zona falsa con una casa ruidosa: aire de +-300 W lento, ruido de contador,
    y otro aparato (horno, nevera...) que salta 300-1500 W de vez en cuando."""
    def __init__(self, clock, seed=1, lag=1.5):
        self.clock, self.rng, self.lag = clock, random.Random(seed), lag
        self.zone_id = "cocina"
        self.on = {"light.barra": False, "light.plafon": True}
        self.bp = {"light.barra": 30, "light.plafon": 80}
        self.changes = []                     # (instante, luz, encendida, brillo): respuesta con retraso
        self.zone = {"plant_mode_lights": []}
        self.current_values = {"brightness_pct": 60, "color_temp_kelvin": 3000}
        self._power = pl.LightPowerLearner({})
        self._calibrating, self.calibration, self.decided = False, None, 0
        self.appliance = []                   # (desde, hasta, vatios)
        t = clock.now()
        for _ in range(12):
            a = t + self.rng.uniform(0, 900)
            self.appliance.append((a, a + self.rng.uniform(20, 300), self.rng.uniform(300, 1500)))
        self.ws = None
        self.calibrate_power = zr.ZoneRunner.calibrate_power.__get__(self)

    def meter(self):
        t = self.clock.now()
        w = 600 + 300 * math.sin(t / 600) + self.rng.gauss(0, 8)
        w += sum(v for a, b, v in self.appliance if a <= t < b)
        state = {e: (False, 0) for e in TRUE_W}
        state.update({"light.plafon": (True, 80)})
        for ts, e, on, bp in self.changes:
            if ts + self.lag <= t:
                state[e] = (on, bp)
        return w + sum(TRUE_W[e] * bp / 100 for e, (on, bp) in state.items() if on)

    def _snapshot_states(self): return {}
    def _reachable(self, states, e): return True
    def all_lights(self): return set(TRUE_W)
    def _current_light_values(self, states, e): return {"on": self.on[e], "brightness_pct": self.bp[e], "color_temp_kelvin": 3000}
    def _turn_off(self, e): self.on[e] = False; self.changes.append((self.clock.now(), e, False, 0))
    def _send_to_light(self, e, bp, ct, hs):
        self.on[e] = True; self.bp[e] = bp; self.changes.append((self.clock.now(), e, True, bp)); return True
    def decide_and_act(self, states=None): self.decided += 1


def _run(seed):
    pl._events.clear()
    c = _Clock(); z = _Zone(c, seed=seed)
    res = z.calibrate_power(sleep=c.sleep, clock=c.now, read_meter=z.meter)
    got = z._power.update_power(c.now(), {"light.plafon": 0.8, "light.barra": 0.6})
    return z, res, got


class Calibration(unittest.TestCase):
    EXPECTED = TRUE_W["light.plafon"] * 0.8 + TRUE_W["light.barra"] * 0.6      # 12 W

    def test_accurate_with_a_noisy_house(self):
        errors = [abs(_run(seed)[2] - self.EXPECTED) for seed in range(15)]
        self.assertLess(max(errors), 4.0, errors)
        self.assertLess(sum(errors) / len(errors), 2.0, errors)

    def test_restores_lights_and_resumes(self):
        z, res, _ = _run(3)
        self.assertEqual(z.on, {"light.barra": False, "light.plafon": True})
        self.assertEqual(z.bp["light.plafon"], 80)
        self.assertFalse(z._calibrating)
        self.assertEqual(z.decided, 1)
        self.assertEqual(res["saltos"], 2 * pl.CALIBRATION_CYCLES)

    def test_other_zone_light_steps_are_skipped(self):
        steps = [(100.0, +1), (200.0, -1)]
        samples = [(t / 2, 500.0 + (12 if 100 <= t / 2 < 200 else 0)) for t in range(0, 600)]
        pl._events.clear()
        pl._note_event(103.0, "light.otra_zona")
        self.assertEqual(pl.step_deltas(samples, steps, {"light.a"}), [12.0])

    def test_zone_does_not_decide_while_calibrating(self):
        z = type("S", (), {})()
        z._calibrating = True
        zr.ZoneRunner._decide_and_act_locked(z, {})
        self.assertIn("calibrando", z.reason)


if __name__ == "__main__":
    unittest.main()
