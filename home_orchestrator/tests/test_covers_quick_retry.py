"""Covers reintenta a los 30 s (no a los 5 min) si la primera decision llego
antes que los datos de HA (2-nov: tras cada reinicio)."""
import os, sys, unittest
from types import SimpleNamespace as N

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import covers_plugin as cp  # noqa: E402


class QuickRetry(unittest.TestCase):
    def test_without_data(self):
        self.assertTrue(cp.needs_quick_retry(N(desired_position=None, reason="sun.sun no disponible -> sin cambios")))
        self.assertTrue(cp.needs_quick_retry(N(desired_position=None, reason="sin evaluar todavia")))

    def test_normal_decisions_wait_the_interval(self):
        self.assertFalse(cp.needs_quick_retry(N(desired_position=0, reason="cierre nocturno -> 0% (cerrado)")))
        self.assertFalse(cp.needs_quick_retry(N(desired_position=None, reason="ninguna regla activa aplica ahora mismo -> sin cambios")))


if __name__ == "__main__":
    unittest.main()
