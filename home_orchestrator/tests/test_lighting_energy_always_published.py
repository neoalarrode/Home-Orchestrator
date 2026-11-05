"""5-nov: el sensor de energia de iluminacion de cada zona publica 0 kWh desde
el principio (sin estado, HA no crea estadisticas y el Panel de Energia avisa)."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from lighting import mqtt_lighting, power_learning  # noqa: E402


class _M:
    def __init__(self): self.pub = {}
    def publish(self, t, p, retain=False): self.pub[t] = p
    def subscribe(self, *a): pass


class _Runner:
    def __init__(self): self._power = power_learning.LightPowerLearner({})
    def group_state(self, states=None): return {"on": False}


class AlwaysPublished(unittest.TestCase):
    def test_zero_energy_published_before_learning(self):
        m = _M()
        z = mqtt_lighting.MqttLightingZone(m, "abc", {"name": "Cocina"})
        z.publish_state(_Runner())
        self.assertEqual(m.pub["homeassistant/sensor/home_orchestrator_lighting/abc_energy/state"], 0.0)


if __name__ == "__main__":
    unittest.main()
