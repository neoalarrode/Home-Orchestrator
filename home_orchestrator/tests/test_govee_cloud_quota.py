"""Cupo de la API Cloud de Govee (10.000 peticiones/dia, 1-nov ya iba por
7.275): no repetir 'turn on' a una bombilla encendida ni mandar cambios que
Lighting ya da por iguales (deriva circadiana de 1 % / 15 K cada minuto)."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import govee.device_manager as dm  # noqa: E402


class CloudQuota(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self._orig = dm.govee_cloud.control
        dm.govee_cloud.control = lambda key, mac, sku, name, value: self.sent.append((name, value)) or True
        self.m = dm.GoveeDeviceManager()
        self.m.set_cloud_identity("b", "AA:BB", "H6008")
        self.m.set_cloud_api_key("k")

    def tearDown(self):
        dm.govee_cloud.control = self._orig

    def test_first_command_sends_everything(self):
        self.m._cloud_control("b", on=True, brightness_pct=55, color_temp_kelvin=4755)
        self.assertEqual([n for n, _ in self.sent], ["turn", "brightness", "colorTem"])

    def test_circadian_drift_is_not_resent(self):
        self.m._cloud_control("b", on=True, brightness_pct=55, color_temp_kelvin=4755)
        self.sent.clear()
        self.m._cloud_control("b", on=True, brightness_pct=54, color_temp_kelvin=4740)
        self.assertEqual(self.sent, [])

    def test_real_change_is_sent_alone(self):
        self.m._cloud_control("b", on=True, brightness_pct=55, color_temp_kelvin=4755)
        self.sent.clear()
        self.m._cloud_control("b", on=True, brightness_pct=40, color_temp_kelvin=4740)
        self.assertEqual(self.sent, [("brightness", 40)])

    def test_drift_accumulates_against_last_sent(self):
        self.m._cloud_control("b", on=True, color_temp_kelvin=4755)
        for k in (4740, 4720, 4705, 4690, 4670, 4650):
            self.m._cloud_control("b", on=True, color_temp_kelvin=k)
        self.assertIn(("colorTem", 4650), self.sent)   # 105 K desde 4755: se manda

    def test_off_then_on(self):
        self.m._cloud_control("b", on=True)
        self.m._cloud_control("b", on=False)
        self.m._cloud_control("b", on=False)
        self.assertEqual(self.sent, [("turn", "on"), ("turn", "off")])


if __name__ == "__main__":
    unittest.main()
