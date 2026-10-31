"""Entradas externas (MQTT, panel, HomeKit): NaN/Inf rechazados y consignas
fuera de los limites de la zona recortadas (hallazgo R2#A de la revision)."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import types  # noqa: E402
sys.modules.setdefault("ha_mqtt", types.ModuleType("ha_mqtt"))  # solo transporte; _as_float no lo usa
import climate.zone_runner as zr  # noqa: E402
import climate.mqtt_climate as mc  # noqa: E402


class _Stub:
    def __init__(self):
        self.zone = {"name": "Z", "min_temp": 16.0, "max_temp": 30.0}
        self._min_humidity, self._max_humidity = 20.0, 80.0
        self.target_humidity = 45.0
        self.decided = 0
        self.sane = zr.ZoneRunner._sane_setpoint.__get__(self)
        self.set_humidity = zr.ZoneRunner.set_humidity.__get__(self)
    def decide_and_act(self): self.decided += 1


class Sanitize(unittest.TestCase):
    def test_nan_and_inf_rejected(self):
        s = _Stub()
        for bad in (float("nan"), float("inf"), float("-inf"), "nan", "basura", None):
            self.assertIsNone(s.sane(bad), bad)

    def test_out_of_range_clamped(self):
        s = _Stub()
        self.assertEqual(s.sane(100), 30.0)
        self.assertEqual(s.sane(-5), 16.0)
        self.assertEqual(s.sane("21.5"), 21.5)

    def test_humidity(self):
        s = _Stub()
        s.set_humidity(float("nan")); self.assertEqual((s.target_humidity, s.decided), (45.0, 0))
        s.set_humidity(95); self.assertEqual(s.target_humidity, 80.0)


class _Msg:
    def __init__(self, payload): self.payload = payload.encode()


class MqttPayload(unittest.TestCase):
    def test_mqtt_rejects_non_finite(self):
        z = mc.MqttClimateZone.__new__(mc.MqttClimateZone)
        z.zone_id = "z"
        for p in ("nan", "inf", "-inf", "abc"):
            self.assertIsNone(mc.MqttClimateZone._as_float(z, _Msg(p), "consigna"), p)
        self.assertEqual(mc.MqttClimateZone._as_float(z, _Msg("21.5"), "consigna"), 21.5)


if __name__ == "__main__":
    unittest.main()
