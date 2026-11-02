"""Zona pospuesta por falta de estado de HA: sin ningun estado (arranque) es
informativo; con estados pero sin sus entidades de presencia, aviso."""
import logging, os, sys, threading, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import lighting.zone_runner as zr  # noqa: E402


class _Stub:
    def __init__(self):
        self.zone = {"presence_entities": ["binary_sensor.presencia"]}
        self.zone_id = "z"; self._lock = threading.Lock()
        self.f = zr.ZoneRunner._decide_and_act_locked.__get__(self)


class PostponedLogLevel(unittest.TestCase):
    def _levels(self, states):
        s = _Stub()
        with self.assertLogs(zr.log, level="INFO") as cm:
            s.f(states)
        self.assertTrue(s.postponed)
        return [r.levelno for r in cm.records]

    def test_empty_snapshot_at_startup_is_info(self):
        self.assertEqual(self._levels({}), [logging.INFO])

    def test_states_without_presence_entities_is_warning(self):
        self.assertEqual(self._levels({"light.x": {"state": "on"}}), [logging.WARNING])


if __name__ == "__main__":
    unittest.main()
