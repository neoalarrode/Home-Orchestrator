"""La lista de tareas (allTimerTask) es del grupo: llega por el principal o por
cualquier miembro, y Energy se suscribe a todas las unidades desde el arranque
(2-nov: el grupo del Despacho nunca la tuvo porque solo se suscribia al
intentar la primera orden por la nube)."""
import json, os, sys, time, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import ecoflow_cloud as ec  # noqa: E402


class _Msg:
    def __init__(self, topic, payload):
        self.topic, self.payload = topic, json.dumps(payload).encode()


def _client():
    c = ec.EcoFlowCloudClient("ak", "sk")
    c._username = "u"
    c._client = mock.Mock()
    return c


TASKS = {"timeTask": [{"isEnable": True, "chgTask": {"devTargetSoc": [{"sn": "MEMBER", "chgFromGridPowerLimited": 150, "targetSoc": 100}]}}]}


class GroupTimerTask(unittest.TestCase):
    def test_list_from_a_member_is_used_for_the_group(self):
        c = _client()
        c._on_message(None, None, _Msg("/open/u/MEMBER/quota", {"allTimerTask": TASKS}))
        self.assertIsNone(c.get_all_timer_task("MAIN"))
        self.assertEqual(c.get_all_timer_task("MAIN", ("MEMBER",)), TASKS)

    def test_newest_list_wins(self):
        c = _client()
        old = json.loads(json.dumps(TASKS)); old["timeTask"][0]["isEnable"] = False
        c._on_message(None, None, _Msg("/open/u/MAIN/quota", {"allTimerTask": old}))
        c._all_timer_task_ts["MAIN"] = time.time() - 60
        c._on_message(None, None, _Msg("/open/u/MEMBER/quota", {"allTimerTask": TASKS}))
        self.assertTrue(c.get_all_timer_task("MAIN", ("MEMBER",))["timeTask"][0]["isEnable"])

    def test_charge_command_uses_member_list(self):
        c = _client()
        c._on_message(None, None, _Msg("/open/u/MEMBER/quota", {"allTimerTask": TASKS}))
        with mock.patch.object(c, "_publish_set", return_value=True) as pub:
            self.assertTrue(c.set_charging_task("MAIN", "MEMBER", power_limit_w=200))
        sent = pub.call_args[0][1]["cfgAllTimerTask"]
        self.assertEqual(sent["timeTask"][0]["chgTask"]["devTargetSoc"][0]["chgFromGridPowerLimited"], 200)
        self.assertEqual(pub.call_args[0][0], "MAIN")


if __name__ == "__main__":
    unittest.main()
