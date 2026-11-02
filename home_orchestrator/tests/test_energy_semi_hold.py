"""EcoFlow en semiautomatico que el seguimiento de carga no controla: el plan no
le activa la descarga (entregaria su carga base sin nadie que la ajuste; 2-nov,
la Max Patio se unio a su grupo con 1200 W de base y sin verse por Bluetooth)."""
import os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import battery_exec as be  # noqa: E402
import main  # noqa: E402


def _bat(i):
    b = be.Battery(id=i, name=i, capacity_wh=2000, source="ecoflow", ecoflow_mode="hybrid")
    b.ecoflow_set_charging_task = mock.Mock(return_value=True)
    b.ecoflow_set_discharging_task = mock.Mock(return_value=True)
    return b


def _dist(action, enabled, power=1200):
    return {"action": action, "per_battery": [
        {"id": "mp", "name": "mp", "soc_pct": 60.0, "power_w": power, "enabled": enabled, "note": "x"}]}


class SemiHoldExecute(unittest.TestCase):
    def setUp(self):
        be._last_command.clear()

    def test_discharge_is_held(self):
        b = _bat("mp")
        lines = be.execute([b], _dist("discharge", True), dry_run=False, semi_hold_ids={"mp"})
        b.ecoflow_set_discharging_task.assert_called_once_with(enable=False)
        b.ecoflow_set_charging_task.assert_called_once_with(enable=False)
        self.assertIn("semiautomatico sin seguimiento", lines[0])

    def test_idle_is_held(self):
        b = _bat("mp")
        be.execute([b], _dist("idle", False, 0), dry_run=False, semi_hold_ids={"mp"})
        b.ecoflow_set_discharging_task.assert_called_once_with(enable=False)

    def test_charge_still_works(self):
        b = _bat("mp")
        be.execute([b], _dist("charge", True, 160), dry_run=False, semi_hold_ids={"mp"})
        b.ecoflow_set_charging_task.assert_called_once_with(enable=True, power_limit_w=160)

    def test_not_held_discharges_as_before(self):
        b = _bat("mp")
        be.execute([b], _dist("discharge", True), dry_run=False)
        b.ecoflow_set_discharging_task.assert_called_once_with(enable=True, power_limit_w=1200)


class SemiHoldIds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False); self.tmp.close(); os.unlink(self.tmp.name)
        self.p = mock.patch.object(main, "ECOFLOW_MODES_PATH", self.tmp.name); self.p.start()
        self.cfg = {"batteries": [
            {"id": "ux", "source": "ecoflow", "ecoflow_sn": "SN_UX", "ecoflow_main_sn": "SN_UX"},
            {"id": "mp", "source": "ecoflow", "ecoflow_sn": "SN_MP", "ecoflow_main_sn": "SN_MP"},
            {"id": "ef", "source": "ecoflow", "ecoflow_sn": "SN_EF", "ecoflow_main_sn": "SN_UX"},
            {"id": "ha", "source": "ha"},
        ]}

    def tearDown(self):
        self.p.stop()
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_semi_not_followed_is_held_and_followed_group_is_not(self):
        for i in ("ux", "mp", "ef"):
            main._note_ecoflow_mode(i, "semi_automated")
        f = mock.Mock(); f._followed = {"ux"}
        with mock.patch.object(main, "_load_follower", f):
            self.assertEqual(main._semi_hold_ids(self.cfg), {"mp"})

    def test_unknown_or_other_mode_is_not_held(self):
        main._note_ecoflow_mode("mp", "smart_meter")
        f = mock.Mock(); f._followed = set()
        with mock.patch.object(main, "_load_follower", f):
            self.assertEqual(main._semi_hold_ids(self.cfg), set())

    def test_mode_survives_ble_loss(self):
        main._note_ecoflow_mode("mp", "semi_automated")
        main._note_ecoflow_mode("mp", None)          # sin lectura BLE: no se olvida
        f = mock.Mock(); f._followed = set()
        with mock.patch.object(main, "_load_follower", f):
            self.assertIn("mp", main._semi_hold_ids(self.cfg))


if __name__ == "__main__":
    unittest.main()
