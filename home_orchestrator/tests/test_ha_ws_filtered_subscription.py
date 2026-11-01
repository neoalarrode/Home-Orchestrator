"""Suscripcion filtrada (`subscribe_entities`) contra un HA simulado que se
porta como el real: rechaza ids que no son de HA, manda el evento inicial ANTES
de la respuesta a la siembra y mete eventos en medio de cualquier respuesta."""
import json, os, sys, threading, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import ha_websocket as hw  # noqa: E402
import websocket as ws_lib  # noqa: E402

STATES = {
    "sensor.t": {"entity_id": "sensor.t", "state": "20.0", "attributes": {}},
    "binary_sensor.p": {"entity_id": "binary_sensor.p", "state": "off", "attributes": {}},
    "sensor.leido": {"entity_id": "sensor.leido", "state": "1", "attributes": {}},
    "sensor.otro": {"entity_id": "sensor.otro", "state": "x", "attributes": {}},
}


class FakeHA:
    def __init__(self, client, after_seed=None):
        self.client, self.after_seed = client, after_seed or []
        self.out = [json.dumps({"type": "auth_required"})]
        self.subscriptions = []
        self.lock = threading.Lock()

    def push(self, obj):
        self.out.append(json.dumps(obj))

    def send(self, raw):
        m = json.loads(raw)
        t = m.get("type")
        if t == "auth":
            self.push({"type": "auth_ok"})
        elif t == "subscribe_entities":
            ids = m["entity_ids"]
            if any(":" in e for e in ids):
                self.push({"id": m["id"], "type": "result", "success": False,
                           "error": {"code": "invalid_format", "message": "invalid entity ID"}})
                return
            self.subscriptions.append(ids)
            # Como HA: primero el evento inicial con las suscritas, luego el ack
            self.push({"id": m["id"], "type": "event", "event": {"a": {
                e: {"s": STATES[e]["state"], "a": {}, "lc": 1.0} for e in ids if e in STATES}}})
            self.push({"id": m["id"], "type": "result", "success": True, "result": None})
        elif t == "subscribe_events":
            self.subscriptions.append("TODO")
            self.push({"id": m["id"], "type": "result", "success": True, "result": None})
        elif t == "unsubscribe_events":
            self.push({"id": m["id"], "type": "result", "success": True, "result": None})
        elif t == "get_states":
            # un evento se cuela antes de la respuesta
            self.push({"id": 1, "type": "event", "event": {"c": {"sensor.t": {"+": {"s": "20.5", "lc": 2.0}}}}})
            self.push({"id": m["id"], "type": "result", "success": True, "result": list(STATES.values())})
            for ev in self.after_seed:
                self.push(ev)

    def recv(self):
        if not self.out:
            if getattr(self.client, "_subs_dirty", False) and self.client._subscribed is not None:
                raise ws_lib.WebSocketTimeoutException()
            raise RuntimeError("fin del guion")
        return self.out.pop(0)

    def close(self):
        pass


def _run(client, ha):
    with mock.patch.object(ws_lib, "create_connection", lambda *a, **k: ha), \
         mock.patch.object(hw, "RESUBSCRIBE_MIN_INTERVAL_SECONDS", 0, create=True):
        try:
            client._connect_and_listen()
        except RuntimeError:
            pass


class FilteredSubscription(unittest.TestCase):
    def client(self):
        c = hw.HAWebSocketClient()
        c.set_watched_entities({"binary_sensor.p", "tuya:abc"}, key="lighting")
        c.set_cached_entities({"sensor.t"}, key="lighting")
        return c

    def test_bridge_refs_are_left_out_and_filtered_mode_is_used(self):
        c = self.client(); ha = FakeHA(c)
        _run(c, ha)
        self.assertEqual(ha.subscriptions[0], ["binary_sensor.p", "sensor.t"])
        self.assertTrue(c._compressed)

    def test_seed_survives_the_initial_event(self):
        c = self.client(); ha = FakeHA(c)
        _run(c, ha)
        self.assertEqual(len(c._states_cache), len(STATES))       # la siembra no se perdio

    def test_entity_read_with_get_state_is_added(self):
        c = self.client()
        def leer():   # tras la siembra, Climate lee una entidad que no declaro
            c.get_state("sensor.leido")
        ha = FakeHA(c)
        orig = ha.send
        def send(raw):
            orig(raw)
            if json.loads(raw).get("type") == "get_states":
                leer()
        ha.send = send
        _run(c, ha)
        self.assertIn("sensor.leido", ha.subscriptions[-1])
        self.assertNotIn("sensor.otro", ha.subscriptions[-1])

    def test_events_during_a_response_are_not_lost(self):
        c = self.client(); ha = FakeHA(c)
        _run(c, ha)
        # el evento colado antes de la respuesta a get_states se aplico (y la
        # siembra posterior manda); no se tomo por la respuesta
        self.assertIn(c._states_cache["sensor.t"]["state"], ("20.0", "20.5"))
        self.assertEqual(c._states_cache["sensor.otro"]["state"], "x")


if __name__ == "__main__":
    unittest.main()
