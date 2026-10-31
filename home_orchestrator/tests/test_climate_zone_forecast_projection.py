"""Grafica de previsión alineada con el motor real (hallazgo R2#D): no usa la
inercia por defecto si el modelo no es fiable, y una hora calentando no dibuja
la zona pasandose de la consigna."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from climate import zone_forecast  # noqa: E402


class _WS:
    def get_history(self, *a, **k): return []
    def call_service(self, *a, **k): return {}
    def get_state(self, e): return None


class _Runner:
    def __init__(self, thermal):
        self.ws, self.bridges, self.hvac_mode = _WS(), None, "heat"
        self.zone = {"priority": "confort", "deadband": 0.3, "min_temp": 15.0, "max_temp": 30.0}
        self.current_temperature = 19.0
        self._thermal = thermal
    def thermal_model_snapshot(self): return self._thermal
    def zone_estimated_power_w(self): return None
    def preset_targets_for_occupancy(self, occupied): return 21.0, None, "Confort"


class Projection(unittest.TestCase):
    def test_heating_never_draws_beyond_setpoint(self):
        r = _Runner({"heating_rate_deg_h": 0.9, "cooling_rate_deg_h": 1.2, "idle_loss_coeff": 0.002, "reliable": True})
        pts = zone_forecast.build_forecast(r, hours_back=0, hours_fwd=5)
        temps = [p["indoor_temp"] for p in pts]
        self.assertEqual(temps[:2], [19.9, 20.8])
        self.assertLessEqual(max(temps), 21.0, temps)   # antes: 21.7

    def test_unreliable_model_does_not_invent_inertia(self):
        r = _Runner({"heating_rate_deg_h": 0.9, "cooling_rate_deg_h": 1.2, "idle_loss_coeff": 0.08, "reliable": False})
        pts = zone_forecast.build_forecast(r, hours_back=0, hours_fwd=3)
        self.assertTrue(all("anticipando" not in p["reason"] for p in pts))


if __name__ == "__main__":
    unittest.main()
