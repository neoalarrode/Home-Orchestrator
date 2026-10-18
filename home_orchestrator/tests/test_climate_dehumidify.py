"""Deshumidificar en reposo/ventilando cuando la humedad supera el umbral, sin el
guard de margen de calor (el usuario quiere secar siempre que la zona esté parada o
fuese a ventilar; nunca mientras calienta/enfría, caso que ni llega a _smart_idle_action).
"""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402


class _Stub:
    def __init__(self, cap, threshold, humidity):
        self._last_full_capability = set(cap)
        self.zone = {"dry_humidity_threshold": threshold}
        self._hum = humidity
        self._smart_idle_action = zr.ZoneRunner._smart_idle_action.__get__(self)
    def _read_humidity_now(self):
        return self._hum


class Dehumidify(unittest.TestCase):
    def test_dries_when_humid_regardless_of_heat_margin(self):
        s = _Stub({"dry", "fan_only"}, 50, 60.0)
        # current_temp muy por debajo del heat_target (antes bloqueaba por heat_margin) -> ahora seca igual
        action, reason = s._smart_idle_action(current_temp=18.0, heat_target=21.0, deadband=0.3)
        self.assertEqual(action, "dry")
        self.assertIn("60", reason)

    def test_vents_when_not_humid(self):
        s = _Stub({"dry", "fan_only"}, 50, 40.0)
        action, _ = s._smart_idle_action(20.0, 21.0, 0.3)
        self.assertEqual(action, "fan_only")

    def test_no_dry_capability_falls_to_fan(self):
        s = _Stub({"fan_only"}, 50, 70.0)
        action, _ = s._smart_idle_action(20.0, None, 0.3)
        self.assertEqual(action, "fan_only")

    def test_idle_when_nothing(self):
        s = _Stub(set(), 50, 70.0)
        action, reason = s._smart_idle_action(20.0, None, 0.3)
        self.assertEqual(action, "idle"); self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
