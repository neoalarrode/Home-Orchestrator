"""5-nov: al deshabilitar la integracion nativa de Tapo el Panel de Energia se
quedo sin "Consumo hoy" de lavadora, lavavajillas, frigorifico... El plugin
publica ahora su propio sensor de energia del dia."""
import os, sys, types, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
# Mismo stub que test_tplink_startup_parallel (kasa no esta en el entorno de
# test), con Module.Light/Energy para que los modulos se puedan consultar.
if "kasa" not in sys.modules:
    _kasa = types.ModuleType("kasa")
    for _n in ("Credentials", "Discover", "Device", "Module",
               "AuthenticationError", "KasaException"):
        setattr(_kasa, _n, type(_n, (Exception,) if _n.endswith(("Error", "Exception")) else (object,), {}))
    sys.modules["kasa"] = _kasa
for _attr in ("Light", "Energy"):
    if not hasattr(sys.modules["kasa"].Module, _attr):
        setattr(sys.modules["kasa"].Module, _attr, _attr)
from kasa import Module  # noqa: E402
import tplink.mqtt_tplink as mt  # noqa: E402


class _Mqtt:
    def __init__(self): self.pub = {}
    def publish(self, topic, payload, retain=False): self.pub[topic] = payload
    def subscribe(self, *a): pass


class _Energy:
    current_consumption = 1800.0
    consumption_today = 1.2345


class _Dev:
    is_on = True
    def __init__(self, energy): self.modules = {Module.Energy: energy}


class _Mgr:
    def __init__(self, dev): self.dev = dev
    def get_device(self, _): return self.dev
    def connected(self, _): return True


def _make(energy):
    m = mt.MqttTplinkDevice.__new__(mt.MqttTplinkDevice)
    m.device_id, m.device_name = "abc", "Lavadora"
    m._mqtt, m._manager = _Mqtt(), _Mgr(_Dev(energy))
    m._device_block = {"identifiers": ["x"], "name": "Lavadora"}
    return m


class EnergySensor(unittest.TestCase):
    def test_discovery_and_state(self):
        m = _make(_Energy())
        m.publish_discovery(); m.publish_state()
        base = "homeassistant/sensor/home_orchestrator_tplink/abc_energy"
        cfg = m._mqtt.pub[f"{base}/config"]
        self.assertEqual(cfg["state_class"], "total_increasing")
        self.assertEqual(cfg["unit_of_measurement"], "kWh")
        self.assertEqual(m._mqtt.pub[f"{base}/state"], 1.234)

    def test_no_today_counter_no_sensor(self):
        e = _Energy(); e.consumption_today = None
        m = _make(e)
        m.publish_discovery(); m.publish_state()
        self.assertFalse(any("abc_energy" in t for t in m._mqtt.pub))


if __name__ == "__main__":
    unittest.main()
