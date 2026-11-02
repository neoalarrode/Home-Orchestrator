"""El aviso "posible fallo del equipo" necesita una lectura nueva del sensor:
2-nov, el FP300 del cuarto de juegos informa cada ~1 h y avisaba a los 30 min
mientras el radiador subia la habitacion de 20 a 22 °C."""
import os, sys, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402

T0 = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)


class _S:
    def __init__(self):
        self.zone = {"name": "Cuarto de Juegos", "current_temp_sensor": "sensor.t"}
        self._equipment_run = None
        self._equipment_failure_suspected = False
        self.sensor_changed = T0 - timedelta(minutes=10)
        self._check_equipment_failure = zr.ZoneRunner._check_equipment_failure.__get__(self)

    def _get_state(self, e):
        return {"state": "21.7", "last_changed": self.sensor_changed.isoformat()}


class StaleSensor(unittest.TestCase):
    def test_no_new_reading_no_warning(self):
        s = _S()
        s._check_equipment_failure("heat", 21.7, T0)
        with self.assertNoLogs("climate.zone_runner", "WARNING"):
            s._check_equipment_failure("heat", 21.7, T0 + timedelta(minutes=45))
        self.assertFalse(s._equipment_failure_suspected)

    def test_new_flat_reading_warns(self):
        s = _S()
        s._check_equipment_failure("heat", 21.7, T0)
        s.sensor_changed = T0 + timedelta(minutes=35)
        with self.assertLogs("climate.zone_runner", "WARNING"):
            s._check_equipment_failure("heat", 21.7, T0 + timedelta(minutes=40))

    def test_sensor_silent_too_long_warns(self):
        s = _S()
        s._check_equipment_failure("heat", 21.7, T0)
        with self.assertLogs("climate.zone_runner", "WARNING"):
            s._check_equipment_failure("heat", 21.7, T0 + timedelta(minutes=zr.EQUIPMENT_FAILURE_MAX_WAIT_FOR_READING_MINUTES + 1))


if __name__ == "__main__":
    unittest.main()
