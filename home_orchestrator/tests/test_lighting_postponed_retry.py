"""Una zona que pospone su primera decision (estado de HA aun sin llegar) se
reintenta a los pocos segundos, no a `reapply_minutes`. La marca aparece DESPUES
de que el hilo periodico empiece a esperar (caso real del arranque)."""
import os, sys, threading, time, types, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import lighting_plugin as lp  # noqa: E402


class _Runner:
    def __init__(self):
        self.postponed = False
        self.calls = 0
    def handle_periodic_reapply(self):
        self.calls += 1
        self.postponed = False
    def to_persisted_state(self):
        return {}


class PostponedRetry(unittest.TestCase):
    def test_retried_long_before_the_interval(self):
        runner = _Runner()
        plugin = types.SimpleNamespace(_runners={"z": runner}, _mqtt_zones={},
                                       _schedule_boost_timer=lambda *a: None)
        stop = threading.Event()
        with mock.patch.object(lp, "POSTPONED_RETRY_SECONDS", 0.02), \
             mock.patch.object(lp.zone_store, "update_zone_state", lambda *a: None):
            t = threading.Thread(target=lp.LightingPlugin._periodic_loop, args=(plugin, "z", 5, runner, stop))
            t.start()
            time.sleep(0.05)
            runner.postponed = True          # la primera decision llega despues
            time.sleep(0.1)
            stop.set(); t.join(1)
        self.assertEqual(runner.calls, 1)    # reintentada ya; el intervalo era de 5 min


if __name__ == "__main__":
    unittest.main()
