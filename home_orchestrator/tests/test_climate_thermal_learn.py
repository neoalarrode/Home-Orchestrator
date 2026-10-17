"""El modelo térmico aprende de la ACCIÓN PROPIA del add-on (action_log), sin
depender de que el actuador publique su on/off en el recorder — que es por lo que
no aprendía con actuadores de bridge (Tuya/tuya_native). Ver
thermal_model._runs_from_action_log / _compute_model_sync(action_log=...).
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from climate import thermal_model  # noqa: E402


class _FakeWS:
    """get_history mínimo: devuelve puntos {state,last_updated(epoch)} sintéticos."""
    def __init__(self, series):
        self._series = series  # {entity_id: [(epoch, value)]}

    def get_history(self, entity_id, start_iso, with_attributes=False):
        return [{"state": str(v), "last_updated": ts} for ts, v in self._series.get(entity_id, [])]


class LearnFromActionLog(unittest.TestCase):
    def test_learns_rate_and_idle_from_own_action_log(self):
        now = datetime.now(timezone.utc)
        base = now - timedelta(hours=6)
        OUT = 10.0            # exterior constante
        RATE = 1.0            # °C/h calentando
        IDLE = 0.03           # coef de pérdida pasiva real (pequeño, bien aislado)
        # transiciones cada 30 min: off, heat, off, heat, ... (>= MIN_RUN_MINUTES=20)
        log = []
        temp = [(base.timestamp(), 20.0)]
        t = base
        T = 20.0
        for i in range(12):
            act = "heat" if i % 2 == 1 else "off"
            log.append((t.timestamp(), act))
            for _ in range(6):  # 6 pasos de 5 min = 30 min
                t = t + timedelta(minutes=5)
                if act == "heat":
                    T = T + RATE * (5 / 60)
                else:
                    T = T + IDLE * (OUT - T) * (5 / 60)
                temp.append((t.timestamp(), round(T, 3)))
        ws = _FakeWS({"sensor.temp": temp, "sensor.out": [(base.timestamp(), OUT), (now.timestamp(), OUT)]})
        zone = {"current_temp_sensor": "sensor.temp", "outdoor_temp_sensor": "sensor.out"}

        model = thermal_model._compute_model_sync(ws, zone, days=5, bridges=None, action_log=log)
        self.assertTrue(model["reliable"], model)
        # tasa de calentamiento aprendida ~1.0 (no el default 0.9)
        self.assertAlmostEqual(model["heating_rate_deg_h"], RATE, delta=0.25)
        # inercia aprendida pequeña (~0.03), MUY por debajo del default 0.08
        self.assertLess(model["idle_loss_coeff"], 0.06)
        self.assertGreater(model["idle_loss_coeff"], 0.0)

    def test_no_action_log_does_not_crash_and_is_unreliable(self):
        # sin log ni switches declarados: cae al método anterior, sin tramos -> no fiable
        now = datetime.now(timezone.utc)
        ws = _FakeWS({"sensor.temp": [(now.timestamp(), 21.0)]})
        model = thermal_model._compute_model_sync(ws, {"current_temp_sensor": "sensor.temp"}, days=5)
        self.assertFalse(model["reliable"])


if __name__ == "__main__":
    unittest.main()
