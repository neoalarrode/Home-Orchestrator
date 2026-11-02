"""Al empezar a seguir, la carga base se manda ANTES de activar la descarga: con
la tarea activa el equipo entrega al momento la base que ya tuviera."""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import main  # noqa: E402

B = {"id": "mp", "name": "Max Patio", "ecoflow_ble_address": "AA", "max_discharge_w": 1200}


class SendOrder(unittest.TestCase):
    def setUp(self):
        main._load_follow_prepared.discard("mp")
        self.calls = []
        self.p = [
            mock.patch.object(main.config_store, "load_config", return_value={"ecoflow_user_id": "u"}),
            mock.patch.object(main.ecoflow_ble, "set_base_load", side_effect=lambda a, u, w: self.calls.append(("base", w)) or True),
            mock.patch.object(main.ecoflow_ble, "set_charging_task", side_effect=lambda *a, **k: self.calls.append(("carga", k["enable"])) or True),
            mock.patch.object(main.ecoflow_ble, "set_discharging_task", side_effect=lambda *a, **k: self.calls.append(("descarga", k["enable"])) or True),
        ]
        for x in self.p: x.start()

    def tearDown(self):
        for x in self.p: x.stop()
        main._load_follow_prepared.discard("mp")

    def test_base_before_enabling_discharge(self):
        self.assertTrue(main._load_follow_send(B, 400))
        self.assertEqual(self.calls, [("base", 400), ("carga", False), ("descarga", True)])

    def test_already_prepared_only_sends_base(self):
        main._load_follow_send(B, 400); self.calls.clear()
        self.assertTrue(main._load_follow_send(B, 500))
        self.assertEqual(self.calls, [("base", 500)])

    def test_base_failure_does_not_enable_discharge(self):
        main.ecoflow_ble.set_base_load.side_effect = lambda *a: False
        self.assertFalse(main._load_follow_send(B, 400))
        self.assertNotIn(("descarga", True), self.calls)


if __name__ == "__main__":
    unittest.main()
