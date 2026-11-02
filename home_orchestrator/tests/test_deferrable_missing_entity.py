"""Carga diferible cuyo interruptor o sensor ya no existe en HA: se avisa en vez
de decir "ENCENDIDA"/"apagada" sin mover nada (2-nov, lavavajillas renombrado)."""
import os, sys, tempfile, unittest
from datetime import datetime

_TMP = tempfile.mkdtemp(prefix="ho_def_")
os.environ["DEFERRABLE_PATH"] = os.path.join(_TMP, "deferrable.json")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import deferrable_exec  # noqa: E402
import ha_client  # noqa: E402
import requests  # noqa: E402

LOAD = {"id": "l1", "name": "Lavavajillas", "enabled": True, "switch_entity": "switch.lavavajillas",
        "power_sensor": "sensor.lavavajillas_consumo_actual", "duration_hours": 1}
NOW = datetime(2026, 11, 2, 0, 30)
SCHED = {"l1": {"occurrences": [{"start": "2026-11-02T00:00:00", "end": "2026-11-02T01:00:00",
                                 "energy_wh": 500, "mode": "cheap"}]}}


class MissingEntity(unittest.TestCase):
    def setUp(self):
        self._orig = (ha_client.get_state, ha_client.turn_on, ha_client.turn_off)
        self.turned = []
        ha_client.turn_on = lambda e: self.turned.append(("on", e))
        ha_client.turn_off = lambda e: self.turned.append(("off", e))
        deferrable_exec._missing_warned_at.clear()

    def tearDown(self):
        ha_client.get_state, ha_client.turn_on, ha_client.turn_off = self._orig

    def _run(self):
        import inspect
        params = inspect.signature(deferrable_exec.execute).parameters
        kwargs = {"loads": [LOAD], "schedules": SCHED, "now": NOW}
        if "live_surplus_w" in params: kwargs["live_surplus_w"] = 0.0
        if "dry_run" in params: kwargs["dry_run"] = False
        return deferrable_exec.execute(**kwargs)

    def test_missing_switch_is_reported_and_not_driven(self):
        def gs(e):
            raise ha_client.HAError(f"Entidad no encontrada: {e}")
        ha_client.get_state = gs
        with self.assertLogs("deferrable_exec", "WARNING"):
            lines = self._run()[0]
        self.assertTrue(any("no existe en Home Assistant" in l for l in lines))
        self.assertEqual(self.turned, [])

    def test_ha_down_is_not_treated_as_missing(self):
        def gs(e):
            raise requests.ConnectionError("HA no responde")
        ha_client.get_state = gs
        lines = self._run()[0]
        self.assertFalse(any("no existe" in l for l in lines))
        self.assertIn(("on", "switch.lavavajillas"), self.turned)


if __name__ == "__main__":
    unittest.main()
