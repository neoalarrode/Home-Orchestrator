"""6-nov: una presencia de un minuto (gato, alguien que pasa) ponia el confort y
arrancaba el radiador de 1,4 kW media hora larga."""
import os, sys, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402

NOW = datetime(2026, 11, 6, 0, 30, tzinfo=timezone.utc)


class _S:
    def __init__(self):
        self.zone = {zr.CONF_PRESENCE_ENTITIES: ["binary_sensor.p"]}
        self.st = None
        self._presence_on_since, self._presence_last_stay = {}, {}
        self._presence_now = zr.ZoneRunner._presence_now.__get__(self)
    def _get_state(self, e): return self.st
    def at(self, state, changed): self.st = {"state": state, "last_changed": changed.isoformat()}


class ShortVisit(unittest.TestCase):
    def setUp(self):
        self._orig = zr._utcnow

    def tearDown(self):
        zr._utcnow = self._orig

    def _now(self, t): zr._utcnow = lambda: t

    def test_brief_presence_is_ignored(self):
        s = _S()
        self._now(NOW); s.at("on", NOW - timedelta(seconds=30))
        self.assertFalse(s._presence_now())
        self._now(NOW + timedelta(seconds=60)); s.at("off", NOW + timedelta(seconds=40))
        self.assertFalse(s._presence_now())                  # sin margen de ausencia tras 70 s

    def test_real_stay_keeps_hold(self):
        s = _S()
        self._now(NOW); s.at("on", NOW - timedelta(minutes=20))
        self.assertTrue(s._presence_now())
        self._now(NOW + timedelta(minutes=10)); s.at("off", NOW + timedelta(minutes=1))
        self.assertTrue(s._presence_now())                   # margen de ausencia de siempre

    def test_after_restart_without_history_keeps_old_behaviour(self):
        s = _S()
        self._now(NOW); s.at("off", NOW - timedelta(minutes=5))
        self.assertTrue(s._presence_now())


if __name__ == "__main__":
    unittest.main()
