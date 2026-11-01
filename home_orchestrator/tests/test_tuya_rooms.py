"""Habitaciones del aspirador Tuya: se mantienen al dia (nube + canal de la app)
en vez de leerse una sola vez al arrancar."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from tuya_native import app_channel, device_manager as dm, mqtt_transport as mt, sweeper_map  # noqa: E402


class _Ctl:
    def __init__(self):
        self.schedule_ids = []
        self.answers = True
        self.robot_rooms = None      # (ids, names) que contesta a roomPropertyQry; None = no lo sabe

    def request(self, req_type, message=None, **kw):
        if not self.answers:
            return None
        if req_type == "roomPropertyQry" and self.robot_rooms is not None:
            ids, names = self.robot_rooms
            return {"reqType": req_type, "success": True, "errCode": 0, "mapId": 3, "num": len(ids),
                    "ids": list(ids), "names": list(names)}
        return {"reqType": req_type, "list": [{"ids": list(self.schedule_ids)}]}


def _device(cloud):
    """TuyaDevice con la nube simulada: `cloud` = {"file": ruta, "rooms": {...}, "fetches": n}."""
    d = dm.TuyaDevice.__new__(dm.TuyaDevice)
    d.api = None; d.device_id = "dev"; d.name = "Robot"; d.ctl = _Ctl(); d.plan = {"main_domain": "vacuum"}
    d.rooms = {}; d.pinned_rooms = {}; d._map_rooms = {}; d._map_file = None; d._live_names = {}
    d._rooms_ts = 0.0; d._rooms_dirty = False; d._robot_known = None
    return d


class TuyaRoomsStayInSync(unittest.TestCase):
    def setUp(self):
        self.cloud = {"file": "h/time_1_mapid_3.bin", "rooms": {"0": "Baño", "1": "Despacho"}, "fetches": 0, "fail": False}
        self._orig = (sweeper_map.latest_map_file, sweeper_map.fetch_rooms, dm.time.time)
        self.now = [1000.0]
        dm.time.time = lambda: self.now[0]

        def latest(api, did):
            return None if self.cloud["fail"] else self.cloud["file"]

        def fetch(api, did, path=None):
            self.cloud["fetches"] += 1
            return {} if self.cloud["fail"] else dict(self.cloud["rooms"])

        sweeper_map.latest_map_file, sweeper_map.fetch_rooms = latest, fetch
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_path = dm.ROBOT_ROOMS_PATH
        dm.ROBOT_ROOMS_PATH = os.path.join(self._tmp.name, "robot_rooms.json")

    def tearDown(self):
        sweeper_map.latest_map_file, sweeper_map.fetch_rooms, dm.time.time = self._orig
        dm.ROBOT_ROOMS_PATH = self._orig_path
        self._tmp.cleanup()

    def test_new_room_known_only_to_the_robot_is_exposed(self):
        """El caso real: el robot ya usa la habitacion 5 y el mapa de la nube aun no la trae."""
        d = _device(self.cloud)
        d.ctl.schedule_ids = [0, 1, 5]
        d.refresh_rooms(force=True)
        self.assertEqual(d.rooms, {"0": "Baño", "1": "Despacho", "5": "Habitación 5"})

    def test_real_name_replaces_placeholder_when_a_newer_map_appears(self):
        d = _device(self.cloud)
        d.ctl.schedule_ids = [5]
        d.refresh_rooms(force=True)
        self.cloud.update(file="h/time_2_mapid_3.bin", rooms={"0": "Baño", "1": "Despacho", "5": "Lavadero"})
        self.now[0] += dm.ROOMS_REFRESH_SECONDS + 1
        d.refresh_rooms()
        self.assertEqual(d.rooms["5"], "Lavadero")

    def test_not_polled_more_often_than_the_interval_and_map_not_redownloaded(self):
        d = _device(self.cloud)
        d.refresh_rooms(force=True)
        for _ in range(20):
            self.now[0] += 30
            d.refresh_rooms()
        self.assertEqual(self.cloud["fetches"], 1)          # mismo fichero: no se vuelve a bajar

    def test_rename_seen_on_the_app_channel_applies_at_once(self):
        d = _device(self.cloud)
        d.refresh_rooms(force=True)
        d.on_app_message("out", {"reqType": "roomPropertySet", "ids": [1], "names": ["Oficina"]})
        self.now[0] += 30                                   # siguiente ciclo de estado, sin esperar 5 min
        d.refresh_rooms()
        self.assertEqual(d.rooms["1"], "Oficina")

    def test_split_or_merge_forces_a_refresh(self):
        d = _device(self.cloud)
        d.refresh_rooms(force=True)
        d.ctl.schedule_ids = [6]
        d.on_app_message("in", {"reqType": "partDivisionRst", "success": True})
        self.now[0] += 30
        d.refresh_rooms()
        self.assertIn("6", d.rooms)

    def test_network_failure_never_wipes_known_rooms(self):
        d = _device(self.cloud)
        d.ctl.schedule_ids = [5]
        d.refresh_rooms(force=True)
        before = dict(d.rooms)
        self.cloud["fail"] = True
        d.ctl.answers = False
        self.now[0] += dm.ROOMS_REFRESH_SECONDS + 1
        d.refresh_rooms()
        self.assertEqual({k: before[k] for k in ("0", "1")}, {k: d.rooms[k] for k in ("0", "1")})

    def test_pinned_names_win_but_new_rooms_still_appear(self):
        d = _device(self.cloud)
        d.pinned_rooms = {"0": "Aseo"}
        d.ctl.schedule_ids = [5]
        d.refresh_rooms(force=True)
        self.assertEqual(d.rooms["0"], "Aseo")
        self.assertIn("5", d.rooms)

    def test_robot_rooms_are_the_main_source_and_skip_the_cloud(self):
        """El caso real (2026-10-03): el robot ya conoce "Entrada"; el mapa de la nube no."""
        d = _device(self.cloud)
        d.ctl.robot_rooms = ([0, 1, 5], ["Baño", "Despacho", "Entrada"])
        d.refresh_rooms(force=True)
        self.assertEqual(d.rooms, {"0": "Baño", "1": "Despacho", "5": "Entrada"})
        self.assertEqual(self.cloud["fetches"], 0)

    def test_robot_rename_is_picked_up_on_the_next_refresh(self):
        d = _device(self.cloud)
        d.ctl.robot_rooms = ([0, 1], ["Baño", "Despacho"])
        d.refresh_rooms(force=True)
        d.ctl.robot_rooms = ([0, 1], ["Baño", "Oficina"])
        d.on_app_message("out", {"reqType": "roomPropertySet", "ids": [1], "names": ["Oficina"]})
        self.now[0] += 30
        d.refresh_rooms()
        self.assertEqual(d.rooms["1"], "Oficina")

    def test_robot_room_without_name_gets_a_placeholder_and_pinned_still_wins(self):
        d = _device(self.cloud)
        d.pinned_rooms = {"0": "Aseo"}
        d.ctl.robot_rooms = ([0, 7], ["Baño", ""])
        d.refresh_rooms(force=True)
        self.assertEqual(d.rooms, {"0": "Aseo", "7": "Habitación 7"})

    def test_robot_silent_keeps_what_it_last_said_even_after_a_restart(self):
        """El robot solo contesta con la app abierta: al cerrarla no se pierde "Entrada"."""
        d = _device(self.cloud)
        d.ctl.robot_rooms = ([0, 5], ["Baño", "Entrada"])
        d.refresh_rooms(force=True)
        d.ctl.answers = False
        self.now[0] += dm.ROOMS_REFRESH_SECONDS + 1
        d.refresh_rooms()
        self.assertEqual(d.rooms, {"0": "Baño", "5": "Entrada"})
        import json_store; json_store.flush_all()
        again = _device(self.cloud)                        # reinicio del add-on
        again.ctl.answers = False
        again.refresh_rooms(force=True)
        self.assertEqual(again.rooms, {"0": "Baño", "5": "Entrada"})
        self.assertEqual(self.cloud["fetches"], 0)

    def test_robot_never_answered_falls_back_to_the_cloud(self):
        d = _device(self.cloud)
        d.refresh_rooms(force=True)
        self.assertEqual(d.rooms, {"0": "Baño", "1": "Despacho"})

    def test_names_pushed_while_the_phone_app_is_open_are_learned(self):
        """La app del movil abre el mapa -> el robot publica roomPropertyQry por el canal."""
        d = _device(self.cloud)
        d.refresh_rooms(force=True)
        d.on_app_message("in", {"reqType": "roomPropertyQry", "success": True, "taskId": "0000000000000",
                                "ids": [0, 1, 5], "names": ["Baño", "Despacho", "Entrada"]})
        self.assertTrue(d._rooms_dirty)
        self.now[0] += 30
        d.refresh_rooms()
        self.assertEqual(d.rooms["5"], "Entrada")

    def test_repeated_identical_reply_does_not_force_refreshes(self):
        d = _device(self.cloud)
        msg = {"reqType": "roomPropertyQry", "success": True, "ids": [0], "names": ["Baño"]}
        d.on_app_message("in", msg)
        d._rooms_dirty = False
        d.on_app_message("in", msg)
        self.assertFalse(d._rooms_dirty)

    def test_segments_are_published_in_the_vacuum_state(self):
        d = _device(self.cloud)
        d.ctl.schedule_ids = [5]
        state = d._vacuum_state({"status": "charging", "battery_percentage": 90})
        self.assertEqual(state["segments"]["5"], "Habitación 5")


class _FakeInfo:
    rc = 0

    def wait_for_publish(self, timeout=None):
        return True


class _FakeClient:
    def __init__(self):
        self.subs = []; self.published = []

    def subscribe(self, topic):
        self.subs.append(topic)

    def publish(self, topic, payload):
        self.published.append((topic, payload))
        return _FakeInfo()


class _Msg:
    def __init__(self, topic, payload):
        self.topic = topic; self.payload = payload


class AppChannelIsBidirectional(unittest.TestCase):
    KEY = b"0123456789abcdef"

    def _channel(self, cb=None):
        ch = app_channel.AppChannel(lambda: {"sid": "s", "ecode": "e", "uid": "u", "device_id": "t"})
        ch._client = _FakeClient()
        ch.register("dev", self.KEY, cb)
        ch._on_connect(ch._client, None, {}, 0)
        return ch

    def test_subscribes_to_both_directions(self):
        ch = self._channel()
        self.assertEqual(sorted(set(ch._client.subs)), ["smart/mb/in/dev", "smart/mb/out/dev"])

    def test_device_messages_reach_the_callback(self):
        seen = []
        ch = self._channel(lambda direction, data: seen.append((direction, data["reqType"])))
        frame, _, _ = mt.build_frame({"reqType": "roomPropertySet", "ids": [1], "names": ["X"]}, self.KEY, protocol=65)
        ch._on_message(None, None, _Msg("smart/mb/in/dev", frame))
        frame, _, _ = mt.build_frame({"reqType": "partDivisionSet"}, self.KEY, protocol=64)
        ch._on_message(None, None, _Msg("smart/mb/out/dev", frame))
        self.assertEqual(seen, [("in", "roomPropertySet"), ("out", "partDivisionSet")])

    def test_request_returns_the_reply_with_the_same_task_id(self):
        ch = self._channel()
        orig = ch.publish

        def publish_and_answer(device_id, message, **kw):
            ok = orig(device_id, message, **kw)
            other, _, _ = mt.build_frame({"reqType": "scheduleQry", "taskId": "otra", "list": []}, self.KEY, protocol=65)
            ch._on_message(None, None, _Msg("smart/mb/in/dev", other))
            reply, _, _ = mt.build_frame({"reqType": "scheduleQry", "taskId": message["taskId"],
                                          "list": [{"ids": [5]}]}, self.KEY, protocol=65)
            ch._on_message(None, None, _Msg("smart/mb/in/dev", reply))
            return ok

        ch.publish = publish_and_answer
        r = ch.request("dev", "scheduleQry", timeout=1)
        self.assertEqual(r["list"], [{"ids": [5]}])
        sent = json.loads(mt.decrypt_frame(ch._client.published[0][1], self.KEY))
        self.assertEqual((sent["protocol"], sent["data"]["reqType"]), (64, "scheduleQry"))

    def test_no_reply_or_garbage_does_not_break(self):
        ch = self._channel()
        ch._on_message(None, None, _Msg("smart/mb/in/dev", b"basura"))
        self.assertIsNone(ch.request("dev", "roomPropertyQry", timeout=0.05))
        self.assertIsNone(ch.request("otro", "scheduleQry", timeout=0.05))


if __name__ == "__main__":
    unittest.main()
