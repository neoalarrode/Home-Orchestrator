"""Un margen de 0 W en el seguimiento de carga es un valor valido (apurar al
maximo aunque haya micro vertidos), no "sin configurar"."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import load_follower as lf  # noqa: E402


class ZeroMargin(unittest.TestCase):
    def test_zero_is_kept(self):
        self.assertEqual(lf._num_or(0, lf.DEFAULT_MARGIN_W), 0.0)
        self.assertEqual(lf._num_or(20, lf.DEFAULT_MARGIN_W), 20.0)

    def test_missing_or_bad_uses_default(self):
        for v in (None, "", "x"):
            self.assertEqual(lf._num_or(v, lf.DEFAULT_MARGIN_W), float(lf.DEFAULT_MARGIN_W))


if __name__ == "__main__":
    unittest.main()
