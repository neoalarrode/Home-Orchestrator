"""5-nov: los sensores de enchufe y de aires de Energy van por MQTT Discovery
(con unique_id, asignables a zona) conservando su entity_id; sin MQTT, REST."""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import energy_mqtt  # noqa: E402
import main  # noqa: E402


class _C:
    connected = True
    def __init__(self): self.pub = []
    def publish(self, t, p, retain=False): self.pub.append((t, p))


class EnergyMqtt(unittest.TestCase):
    def setUp(self):
        energy_mqtt._announced.clear()

    def test_keeps_entity_id_and_announces_once(self):
        c = _C(); deleted = []
        with mock.patch.object(energy_mqtt, "_get_client", lambda: c), \
                mock.patch("ha_client.delete_state", lambda e: deleted.append(e)):
            attrs = {"device_class": "power", "unit_of_measurement": "W", "friendly_name": "EX01"}
            self.assertTrue(energy_mqtt.publish("sensor.battery_orchestrator_outlet_x_2", 160, attrs))
            self.assertTrue(energy_mqtt.publish("sensor.battery_orchestrator_outlet_x_2", 161, attrs))
        cfg = [p for t, p in c.pub if t.endswith("/config")]
        self.assertEqual(len(cfg), 1)
        self.assertEqual(cfg[0]["default_entity_id"], "sensor.battery_orchestrator_outlet_x_2")
        self.assertTrue(cfg[0]["unique_id"])
        self.assertEqual(deleted, ["sensor.battery_orchestrator_outlet_x_2"])

    def test_without_mqtt_falls_back_to_rest(self):
        sent = []
        main._last_published_at.clear(); main._last_published_value.clear()
        with mock.patch.object(energy_mqtt, "_get_client", lambda: None), \
                mock.patch.object(main.ha_client, "publish_sensor", lambda e, s, a: sent.append(e)):
            main._publish_sensor_throttled("sensor.y", 1, {"unit_of_measurement": "W"}, mqtt=True)
        self.assertEqual(sent, ["sensor.y"])


if __name__ == "__main__":
    unittest.main()
