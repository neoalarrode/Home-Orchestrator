"""Presencia con margen de ausencia (30 min): una salida corta no cambia el
preset (subia y bajaba la consigna sin ahorrar)."""
import os, sys, unittest
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402


class _Stub:
    def __init__(self, states):
        self.zone = {"presence_entities": list(states)}
        self._states = states
        self.presence = zr.ZoneRunner._presence_now.__get__(self)
    def _get_state(self, e): return self._states[e]


def _st(state, minutes_ago):
    return {"state": state, "last_changed": (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()}


class PresenceHold(unittest.TestCase):
    def test_present(self):
        self.assertTrue(_Stub({"p": _st("on", 1)}).presence())

    def test_left_five_minutes_ago_still_occupied(self):
        self.assertTrue(_Stub({"p": _st("off", 5)}).presence())

    def test_left_long_ago_is_empty(self):
        self.assertFalse(_Stub({"p": _st("off", 45)}).presence())

    def test_epoch_timestamp(self):
        st = {"state": "off", "last_changed": (datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp()}
        self.assertTrue(_Stub({"p": st}).presence())

    def test_unavailable_sensor_is_unknown(self):
        self.assertIsNone(_Stub({"p": {"state": "unavailable"}}).presence())


if __name__ == "__main__":
    unittest.main()
