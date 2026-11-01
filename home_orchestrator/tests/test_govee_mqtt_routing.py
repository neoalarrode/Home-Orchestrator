"""Comandos de HA via MQTT a un Govee: deben pasar por el handle (LAN o nube),
no directos al gestor LAN. Caso real 1-nov: dispositivo solo-nube 3754a189
daba KeyError "dispositivo Govee desconocido" al apagarlo desde HA."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from govee.device_manager import GoveeDeviceManager  # noqa: E402
from govee.mqtt_govee import MqttGoveeDevice  # noqa: E402


class _Msg:
    def __init__(self, p): self.payload = p.encode()


class _Mqtt:
    def publish(self, *a, **k): pass


class _Sync:
    def submit(self, fn): fn()


class CloudOnlyFromHA(unittest.TestCase):
    def setUp(self):
        self.m = GoveeDeviceManager()
        self.m.set_cloud_identity("3754a189", "AA:BB", "H6008")
        self.sent = []
        self.m._cloud_control = lambda d, **kw: self.sent.append((d, kw)) or True
        # Sin __init__: otros tests sustituyen `ha_mqtt` por un stub sin el worker.
        self.dev = MqttGoveeDevice.__new__(MqttGoveeDevice)
        self.dev._mqtt, self.dev._manager, self.dev.device_id = _Mqtt(), self.m, "3754a189"
        self.dev._commands = _Sync()

    def test_off_goes_to_cloud(self):
        self.dev._on_power(None, None, _Msg("OFF"))
        self.assertEqual(self.sent, [("3754a189", {"on": False})])

    def test_on_and_brightness_go_to_cloud(self):
        self.dev._on_power(None, None, _Msg("ON"))
        self.dev._on_brightness(None, None, _Msg("40"))
        self.assertTrue(all(kw["on"] for _, kw in self.sent))
        self.assertEqual(self.sent[-1][1]["brightness_pct"], 40)


if __name__ == "__main__":
    unittest.main()
