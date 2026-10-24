"""_smart_idle_action (solo se llama con acción idle: la zona en banda, sin calentar
ni enfriar):
  - deshumidifica si hay humedad y el equipo puede (dry), en reposo/ventilando;
  - en un AC/bomba de calor (soporta frío o dry) NO ventila como respaldo del idle
    (gasta ventilador sin enfriar y es peor que dejar el inverter al ralentí) -> idle;
  - un ventilador puro (fan_only sin compresor) sí conserva el fan_only.
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


class SmartIdle(unittest.TestCase):
    def test_ac_dries_when_humid(self):
        s = _Stub({"cool", "dry", "fan_only"}, 50, 60.0)
        action, reason = s._smart_idle_action(18.0, 21.0, 0.3)  # temp baja: ya no bloquea
        self.assertEqual(action, "dry"); self.assertIn("60", reason)

    def test_ac_in_band_not_humid_goes_idle_not_fan(self):
        # AC (soporta dry/cool): en banda y seco -> idle al ralentí, NO ventilar
        s = _Stub({"cool", "dry", "fan_only"}, 50, 40.0)
        action, reason = s._smart_idle_action(21.0, 21.0, 0.3)
        self.assertEqual(action, "idle"); self.assertIsNone(reason)

    def test_ac_without_dry_still_idle_not_fan(self):
        # AC que enfría pero sin modo dry: igualmente idle, no fan
        s = _Stub({"cool", "fan_only"}, 50, None)
        action, _ = s._smart_idle_action(21.0, None, 0.3)
        self.assertEqual(action, "idle")

    def test_pure_fan_still_ventilates(self):
        # ventilador puro (sin frío ni dry): conserva el fan_only como respaldo
        s = _Stub({"fan_only"}, 50, 40.0)
        action, _ = s._smart_idle_action(21.0, None, 0.3)
        self.assertEqual(action, "fan_only")

    def test_idle_when_nothing(self):
        s = _Stub(set(), 50, 70.0)
        action, reason = s._smart_idle_action(20.0, None, 0.3)
        self.assertEqual(action, "idle"); self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
