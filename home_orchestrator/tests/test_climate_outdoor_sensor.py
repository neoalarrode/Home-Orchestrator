"""El sensor exterior propio corrige la hora actual de la previsión, salvo que
discrepe tanto (>5 C) que lo raro sea el sensor: uno al sol marcaba 35.2 C con la
previsión en 21-23 (31-oct) y falseaba la modulación y la anticipación."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.outdoor as outdoor  # noqa: E402


class _WS:
    def __init__(self, sensor_value, forecast):
        self.sensor, self.forecast = sensor_value, forecast
    def call_service(self, *a, **k):
        return {"weather.casa": {"forecast": [{"temperature": t} for t in self.forecast]}}
    def get_state(self, e):
        return {"state": str(self.sensor)}


ZONE = {"outdoor_temp_sensor": "sensor.patio"}


class OutdoorSensor(unittest.TestCase):
    def test_sensor_in_the_sun_is_ignored(self):
        f = outdoor.get_outdoor_forecast(_WS(35.2, [22.0, 23.0, 23.1]), ZONE, "weather.casa", 3)
        self.assertEqual(f, [22.0, 23.0, 23.1])

    def test_close_sensor_corrects_current_hour(self):
        f = outdoor.get_outdoor_forecast(_WS(19.5, [22.0, 23.0, 23.1]), ZONE, "weather.casa", 3)
        self.assertEqual(f[0], 19.5)


if __name__ == "__main__":
    unittest.main()
