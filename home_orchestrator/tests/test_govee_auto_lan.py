"""Una bombilla Govee dada de alta solo por la nube que responde por LAN pasa a
LAN sola, y si cambia de IP se le actualiza (2-nov: "Salon delante" respondia en
la 192.168.1.7 y seguia por la nube; decision del usuario: automatico)."""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import govee_plugin  # noqa: E402


class AutoLan(unittest.TestCase):
    def setUp(self):
        self.devices = [
            {"id": "a", "config": {"name": "Salon delante", "govee_device_mac": "99:8B:D5:38:31:39:37:1E", "host": ""}},
            {"id": "b", "config": {"name": "Patio", "govee_device_mac": "7E:4E", "host": "192.168.1.50"}},
        ]

        def update(i, cfg):
            for d in self.devices:
                if d["id"] == i:
                    d["config"].update(cfg)
                    return d
        self.p = [mock.patch.object(govee_plugin.govee_store, "load_devices", lambda: [dict(d, config=dict(d["config"])) for d in self.devices]),
                  mock.patch.object(govee_plugin.govee_store, "update_device", update)]
        for x in self.p:
            x.start()
        self.plugin = govee_plugin.GoveePlugin.__new__(govee_plugin.GoveePlugin)
        self.plugin._stop_device = mock.Mock()
        self.plugin._start_device = mock.Mock()

    def tearDown(self):
        for x in self.p:
            x.stop()

    def test_cloud_only_device_gets_its_lan_ip(self):
        changed = self.plugin.adopt_lan_addresses([
            {"ip": "192.168.1.7", "device": "99:8b:d5:38:31:39:37:1e", "sku": "H6076"},
            {"ip": "192.168.1.129", "device": "0E:B9", "sku": "H60A4"},          # no dada de alta: nada
        ])
        self.assertEqual(changed, ["a"])
        self.assertEqual(self.devices[0]["config"]["host"], "192.168.1.7")
        self.plugin._start_device.assert_called_once()

    def test_same_ip_is_left_alone_and_new_ip_is_updated(self):
        self.assertEqual(self.plugin.adopt_lan_addresses([{"ip": "192.168.1.50", "device": "7E:4E"}]), [])
        self.assertEqual(self.plugin.adopt_lan_addresses([{"ip": "192.168.1.51", "device": "7E:4E"}]), ["b"])
        self.assertEqual(self.devices[1]["config"]["host"], "192.168.1.51")


if __name__ == "__main__":
    unittest.main()
