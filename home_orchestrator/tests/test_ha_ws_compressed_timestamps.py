"""Formato comprimido de subscribe_entities: HA omite `lu` cuando coincide con
`lc` (altas) y no lo manda cuando cambia `lc` (cambios). La cache debe dejar
`last_updated` al dia en ambos casos."""
import os, sys, threading, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import ha_websocket  # noqa: E402


def _client():
    c = ha_websocket.HAWebSocketClient.__new__(ha_websocket.HAWebSocketClient)
    c._states_lock = threading.Lock()
    c._states_cache = {}
    return c


class CompressedTimestamps(unittest.TestCase):
    def test_add_without_lu_takes_lc(self):
        c = _client()
        c._apply_compressed({"a": {"sensor.t": {"s": "20.8", "a": {}, "lc": 100.0}}})
        self.assertEqual(c._states_cache["sensor.t"]["last_updated"], 100.0)

    def test_change_with_lc_moves_last_updated(self):
        c = _client()
        c._states_cache["sensor.t"] = {"entity_id": "sensor.t", "state": "20.8", "attributes": {},
                                       "last_changed": "2026-11-01T16:03:44+00:00",
                                       "last_updated": "2026-11-01T16:03:44+00:00"}
        c._apply_compressed({"c": {"sensor.t": {"+": {"s": "20.15", "lc": 1793550000.0}}}})
        st = c._states_cache["sensor.t"]
        self.assertEqual((st["state"], st["last_changed"], st["last_updated"]), ("20.15", 1793550000.0, 1793550000.0))

    def test_change_with_only_lu(self):
        c = _client()
        c._states_cache["sensor.t"] = {"entity_id": "sensor.t", "state": "20.8", "attributes": {},
                                       "last_changed": 1.0, "last_updated": 1.0}
        c._apply_compressed({"c": {"sensor.t": {"+": {"lu": 5.0, "a": {"x": 1}}}}})
        st = c._states_cache["sensor.t"]
        self.assertEqual((st["last_changed"], st["last_updated"]), (1.0, 5.0))


if __name__ == "__main__":
    unittest.main()
