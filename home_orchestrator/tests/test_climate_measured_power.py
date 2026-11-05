"""Medidores de potencia de los radiadores: (1) la potencia que gasta la zona al
actuar sale de su potencia EN MARCHA (no de la lectura en vivo de un equipo
parado, ~1 W); (2) el margen de arranque usa el consumo como verdad."""
import os, sys, unittest
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402

T0 = datetime(2026, 10, 31, 22, 0, tzinfo=timezone.utc)


class _Stub:
    def __init__(self, live, zone_power=None, actuators=("tuya:rad",)):
        self.live = live
        self.zone = {"name": "Cuarto", "actuator_power": {"tuya:rad": {"sensor": "sensor.rad_power"}}}
        self._actuators = list(actuators)
        self._running_power, self._power_model = {}, {}
        self._zone_power = zone_power or {}
        self._delegate_activation_margin, self._activation_probe = {}, {}
        self._activation_ok_streak = {}
        for n in ("_zone_estimated_power_w", "_measured_power_w", "_actuator_power_w", "_learn_activation_margin",
                  "_relax_activation_margin"):
            setattr(self, n, getattr(zr.ZoneRunner, n).__get__(self))
    def _climate_actuators(self): return self._actuators
    def _get_state(self, e):
        return {"state": str(self.live)} if e == "sensor.rad_power" else None


class MeasuredPower(unittest.TestCase):
    def test_idle_reading_is_not_the_zone_power(self):
        self.assertIsNone(_Stub(0.8).est() if hasattr(_Stub, "est") else _Stub(0.8)._zone_estimated_power_w())

    def test_running_power_learned_and_kept(self):
        s = _Stub(1480.0)
        self.assertEqual(s._zone_estimated_power_w(), 1480.0)
        s.live = 0.8                                   # se para: se conserva lo aprendido
        self.assertEqual(s._zone_estimated_power_w(), 1480.0)

    def test_unknown_actuator_falls_back_to_zone_learning(self):
        s = _Stub(1480.0, zone_power={"reliable": True, "learned_power_w": 482}, actuators=("tuya:rad", "tuya:ac"))
        self.assertEqual(s._zone_estimated_power_w(), 482.0)


class MarginByPower(unittest.TestCase):
    def test_no_consumption_raises_margin(self):
        s = _Stub(0.8)
        attrs = {"current_temperature": 22, "target_temp_step": 1.0}
        s._learn_activation_margin("tuya:rad", "heat", attrs, 20.5, T0)
        self.assertEqual(s._learn_activation_margin("tuya:rad", "heat", attrs, 20.5, T0 + timedelta(minutes=11)), 1.0)

    def test_cycling_radiator_is_not_penalised(self):
        s = _Stub(0.8)
        attrs = {"current_temperature": 22, "target_temp_step": 1.0}
        s._learn_activation_margin("tuya:rad", "heat", attrs, 20.5, T0)
        s.live = 1450.0                                 # consume un rato (ciclado de su termostato)
        s._learn_activation_margin("tuya:rad", "heat", attrs, 20.5, T0 + timedelta(minutes=4))
        s.live = 0.8
        self.assertEqual(s._learn_activation_margin("tuya:rad", "heat", attrs, 20.5, T0 + timedelta(minutes=11)), 0.0)


if __name__ == "__main__":
    unittest.main()
