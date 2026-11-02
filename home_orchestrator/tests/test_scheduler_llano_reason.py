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


class LastLlanoBeforeValle(unittest.TestCase):
    """2-nov 23:00: con la carga en valle por delante, el colchon no frena la
    descarga en el ultimo llano (se repone mas barato y a mas potencia)."""
    def _first(self, tiers, soc, grid):
        n = len(tiers)
        plan, _ = scheduler.build_plan(
            now=NOW, pv_forecast_w=[0.0] * n, load_forecast_w=[800.0] * n, current_soc_wh=soc,
            total_capacity_wh=9600, max_charge_w=1200, max_discharge_w=1200, min_soc_wh=1000,
            prices_tiers=[(0.094 if t == "llano" else 0.075 if t == "valle" else 0.173, t) for t in tiers],
            allow_grid_charging=grid, reserve_safety_margin_wh=1400)
        return plan[0]

    def test_discharges_into_the_cushion_when_valle_recharges(self):
        hp = self._first(["llano", "valle", "valle"], 1900, grid=True)
        self.assertAlmostEqual(hp.discharge_w, 800.0)
        self.assertEqual(hp.reason, "descarga para cubrir consumo en llano")

    def test_keeps_cushion_without_grid_charging(self):
        self.assertEqual(self._first(["llano", "valle", "valle"], 1900, grid=False).discharge_w, 0)

    def test_keeps_cushion_with_punta_ahead(self):
        hp = self._first(["llano", "punta", "valle"], 3000, grid=True)
        self.assertEqual(hp.discharge_w, 0)   # 3000 - minimo - punta - colchon < 0


if __name__ == "__main__":
    unittest.main()
