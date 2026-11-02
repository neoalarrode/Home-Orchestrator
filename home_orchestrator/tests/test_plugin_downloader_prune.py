"""Al instalar una version de un plugin se borran las antiguas, salvo las
KEEP_VERSIONS mas recientes, la nueva y la que esta en uso (2-nov: 76 versiones
de Energy guardadas, 292 MB)."""
import os, sys, tempfile, time, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import plugin_downloader as pd  # noqa: E402


class Prune(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(pd, "DATA_DIR", self.tmp.name); self.p.start()
        self.base = os.path.join(self.tmp.name, "battery"); os.makedirs(self.base)
        now = time.time()
        for i in range(8):                                  # v1 (vieja) .. v8 (reciente)
            d = os.path.join(self.base, f"v{i + 1}"); os.makedirs(d)
            os.utime(d, (now - 1000 + i * 10, now - 1000 + i * 10))

    def tearDown(self):
        self.p.stop(); self.tmp.cleanup()

    def _left(self):
        return sorted(d for d in os.listdir(self.base) if d != "current")

    def test_keeps_recent_new_and_in_use(self):
        in_use = os.path.realpath(os.path.join(self.base, "v2"))     # el proceso aun corre la v2
        new = os.path.realpath(os.path.join(self.base, "v8"))
        pd._prune_old_versions("battery", keep={new, in_use})
        self.assertEqual(self._left(), ["v2", "v6", "v7", "v8"])

    def test_never_raises(self):
        pd._prune_old_versions("no_existe", keep=set())


if __name__ == "__main__":
    unittest.main()
