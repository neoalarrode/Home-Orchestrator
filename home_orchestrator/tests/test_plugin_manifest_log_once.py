"""El aviso de version fijada por el manifiesto sale una vez por plugin y tag,
no en cada fusion del catalogo (~6 por arranque)."""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import plugin_manifest as pm  # noqa: E402

BASE = {"climate": {"tag": "v0.1", "downloadable": True}}


class LogOnce(unittest.TestCase):
    def setUp(self):
        pm._pins_logged.clear()

    def _run(self, tag):
        with mock.patch.object(pm, "fetch", return_value={"climate": {"tag": tag}}):
            return pm.catalogo_efectivo(BASE)

    def test_same_pin_logged_once(self):
        with self.assertLogs(pm.log, level="INFO") as cm:
            for _ in range(6):
                self.assertEqual(self._run("v0.9")["climate"]["tag"], "v0.9")
        self.assertEqual(sum("pinea" in m for m in cm.output), 1)

    def test_new_pin_logged_again(self):
        with self.assertLogs(pm.log, level="INFO") as cm:
            self._run("v0.9"); self._run("v1.0")
        self.assertEqual(sum("pinea" in m for m in cm.output), 2)


if __name__ == "__main__":
    unittest.main()
