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



class ZeroDeadband(unittest.TestCase):
    def _sent(self, deadband):
        sent = []
        cfg = [{"id": "u"}]
        ctx = {"mode": "on", "followers": [{"id": "u", "max_w": 1200, "available_wh": 2000, "soc": 50}],
               "batteries_cfg": cfg, "discharge_allowed": True, "margin_w": 0}
        if deadband is not None:
            ctx["deadband_w"] = deadband
        f = lf.LoadFollower(lambda: ctx, lambda b, w: sent.append(w) or True)
        f.asynchronous = False
        f._apply({"u": 400.0}, ctx, "t")
        f._apply({"u": 406.0}, ctx, "t")      # 6 W de diferencia
        f._apply({"u": 406.4}, ctx, "t")      # misma orden
        return sent

    def test_zero_follows_every_small_change(self):
        self.assertEqual(self._sent(0), [400.0, 406.0])

    def test_default_still_ignores_small_changes(self):
        self.assertEqual(self._sent(None), [400.0])


if __name__ == "__main__":
    unittest.main()
