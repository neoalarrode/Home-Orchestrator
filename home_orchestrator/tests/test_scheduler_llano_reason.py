"""Motivo de "no descargar en llano": en su minimo, reservado para una punta
posterior o colchon de seguridad (antes siempre "reservado para punta posterior")."""
import os, sys, unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import scheduler  # noqa: E402

NOW = datetime(2026, 11, 2, 23, 0)


def _plan(tiers, soc, margin=0.0, load=800.0):
    n = len(tiers)
    plan, _ = scheduler.build_plan(
        now=NOW, pv_forecast_w=[0.0] * n, load_forecast_w=[load] * n, current_soc_wh=soc,
        total_capacity_wh=9600, max_charge_w=1200, max_discharge_w=1200, min_soc_wh=1000,
        prices_tiers=[(0.1, t) for t in tiers], allow_grid_charging=False, reserve_safety_margin_wh=margin)
    return plan[0].reason


class LlanoReason(unittest.TestCase):
    def test_at_minimum_before_valle(self):
        self.assertEqual(_plan(["llano", "valle"], 1000), "sin descargar en llano: bateria en su minimo")

    def test_reserved_for_later_punta(self):
        self.assertEqual(_plan(["llano", "punta", "punta"], 1500, load=2000),
                         "sin descargar en llano: reservado para punta posterior")

    def test_safety_margin(self):
        self.assertEqual(_plan(["llano", "valle"], 1300, margin=500),
                         "sin descargar en llano: reservado como colchon de seguridad")


if __name__ == "__main__":
    unittest.main()
