"""6-nov: calibracion forzada del consumo de una zona de luz DENTRO de Lighting:
enciende/apaga sus luces varias veces, promedia el consumo de la casa y lo
guarda en el aprendizaje; con ruido de aire/baterias, y dejando las luces como
estaban."""
import os, random, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import lighting.zone_runner as zr  # noqa: E402
from lighting import power_learning as pl  # noqa: E402

TRUE_W = {"light.barra": 4.0, "light.plafon": 12.0}


class _Clock:
    def __init__(self):
        import time
        self.t = time.time()
    def now(self): return self.t
    def sleep(self, s): self.t += s


class _Zone:
    def __init__(self, clock, noise=40.0):
        self.clock, self.noise = clock, noise
        self.zone_id = "cocina"
        self.on = {"light.barra": False, "light.plafon": True}
        self.bp = {"light.barra": 30, "light.plafon": 80}
        self.rng = random.Random(1)
        self.ws = self
        self.zone = {"plant_mode_lights": []}
        self.current_values = {"brightness_pct": 60, "color_temp_kelvin": 3000}
        self._power = pl.LightPowerLearner({})
        self._calibrating = False
        self.calibration = None
        self.decided = 0
        self.calibrate_power = zr.ZoneRunner.calibrate_power.__get__(self)
    # --- HA falso: consumo de la casa = base + aire (oscila lento) + luces + ruido
    def get_state(self, e):
        if e == pl.GRID_SIGNAL_ENTITY_ID:
            return None
        import math
        t = self.clock.now()
        w = 600 + 300 * math.sin(t / 600) + self.rng.gauss(0, self.noise)
        w += sum(TRUE_W[x] * self.bp[x] / 100 for x in TRUE_W if self.on[x])
        return {"state": str(w)}
    def _snapshot_states(self): return {}
    def _reachable(self, states, e): return True
    def all_lights(self): return set(TRUE_W)
    def _current_light_values(self, states, e): return {"on": self.on[e], "brightness_pct": self.bp[e], "color_temp_kelvin": 3000}
    def _turn_off(self, e): self.on[e] = False
    def _send_to_light(self, e, bp, ct, hs): self.on[e] = True; self.bp[e] = bp; return True
    def decide_and_act(self, states=None): self.decided += 1


class Calibration(unittest.TestCase):
    def test_learns_zone_power_through_noise_and_restores(self):
        clock = _Clock()
        z = _Zone(clock)
        res = z.calibrate_power(cycles=9, half_s=40, skip_s=12, sleep=clock.sleep, clock=clock.now)
        # a los brillos de la calibracion (plafon 80 %, barra con el de la curva 60 %)
        expected = TRUE_W["light.plafon"] * 0.8 + TRUE_W["light.barra"] * 0.6
        got = z._power.update_power(clock.now(), {"light.plafon": 0.8, "light.barra": 0.6})
        self.assertAlmostEqual(got, expected, delta=6.0)   # ruido de 40 W y aire de +-300 W
        self.assertEqual(z.on, {"light.barra": False, "light.plafon": True})      # como estaban
        self.assertEqual(z.bp["light.plafon"], 80)
        self.assertFalse(z._calibrating)
        self.assertEqual(z.decided, 1)
        self.assertIsNotNone(res["vatios"])

    def test_zone_does_not_decide_while_calibrating(self):
        z = type("S", (), {})()
        z._calibrating = True
        zr.ZoneRunner._decide_and_act_locked(z, {})
        self.assertIn("calibrando", z.reason)


if __name__ == "__main__":
    unittest.main()
