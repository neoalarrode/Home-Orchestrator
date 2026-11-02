"""Los sensores de Energy se publican en cuanto cambian (3-nov: antes cada 10 s,
15 s o 2 min); un valor igual solo se reenvia cada PUBLISH_HEARTBEAT_SECONDS."""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import main  # noqa: E402


class PublishOnChange(unittest.TestCase):
    def setUp(self):
        main._last_published_at.clear(); main._last_published_value.clear()
        self.sent = []
        self.now = 1000.0
        self.p = [mock.patch.object(main.ha_client, "publish_sensor", lambda e, s, a: self.sent.append(s)),
                  mock.patch.object(main.time, "time", lambda: self.now)]
        for x in self.p: x.start()

    def tearDown(self):
        for x in self.p: x.stop()

    def pub(self, v):
        main._publish_sensor_throttled("sensor.x", v, {"unit_of_measurement": "W"})

    def test_changes_go_out_immediately(self):
        for i, v in enumerate((100, 120, 95)):
            self.now = 1000 + i * 0.5
            self.pub(v)
        self.assertEqual(self.sent, [100, 120, 95])

    def test_same_value_only_as_heartbeat(self):
        self.pub(100); self.now += 1; self.pub(100)
        self.assertEqual(self.sent, [100])
        self.now += main.PUBLISH_HEARTBEAT_SECONDS
        self.pub(100)
        self.assertEqual(self.sent, [100, 100])


if __name__ == "__main__":
    unittest.main()
