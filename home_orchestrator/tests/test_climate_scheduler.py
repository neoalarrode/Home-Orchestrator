"""Pre-climatizado dimensionado por duracion del bloque caro (scheduler.py).

Hasta ahora el pre-banqueo estaba topado a UN deadband; ahora se dimensiona para
coastear el bloque de punta entero con la inercia (hasta el limite de seguridad,
nunca menos que antes, nunca si no hay previsión/inercia). Estos son los primeros
tests de `decide_action`/preheat del repo.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from climate import scheduler  # noqa: E402


def _grid(hours_tiers, solar_w=0.0):
    """grid_forecast: lista de {tier, solar_surplus_w} empezando por 'ahora'."""
    return [{"tier": t, "solar_surplus_w": solar_w} for t in hours_tiers]


class CoastBoost(unittest.TestCase):
    def test_long_cold_block_banks_more_than_one_deadband(self):
        base = 0.3 * scheduler._retention_factor(0.05)
        boost = scheduler._coast_boost(True, 20.0, 0.3, 0.05, [5.0] * 6, block_offset=1, block_hours=4)
        self.assertGreater(boost, base * 2)          # claramente mas que el deadband de antes

    def test_no_outdoor_forecast_falls_back_to_deadband(self):
        base = 0.3 * scheduler._retention_factor(0.05)
        self.assertAlmostEqual(scheduler._coast_boost(True, 20.0, 0.3, 0.05, [], 1, 4), base)

    def test_no_block_falls_back_to_deadband(self):
        base = 0.3 * scheduler._retention_factor(0.05)
        self.assertAlmostEqual(scheduler._coast_boost(True, 20.0, 0.3, 0.05, [5.0] * 6, 1, 0), base)

    def test_outdoor_already_helping_does_not_inflate(self):
        # calefaccion pero fuera hace MAS calor que la consigna: no hay deriva que
        # compensar -> se queda en el deadband base, no banquea de mas.
        base = 0.3 * scheduler._retention_factor(0.05)
        self.assertAlmostEqual(scheduler._coast_boost(True, 20.0, 0.3, 0.05, [25.0] * 6, 1, 4), base)


class PriceAnticipationSizing(unittest.TestCase):
    def test_sizes_preheat_to_cover_long_punta_block(self):
        # bloque de 4h de punta sin sol a partir de la hora 1; zona retiene bien,
        # fuera hace frio. current 20.5 esta por encima del target+deadband (20.3)
        # -> con el deadband de antes NO precalentaria; con el dimensionado SI.
        gf = _grid(["llano", "punta", "punta", "punta", "punta"])
        action, reason = scheduler._price_anticipation_preheat(
            True, 20.5, 20.0, 0.3, "llano", gf, 1500.0, 23.0, 12.0,
            idle_loss_coeff=0.05, outdoor_forecast=[5.0] * 6,
        )
        self.assertEqual(action, "heat")
        self.assertIn("punta", reason)

    def test_old_behaviour_without_outdoor_is_single_deadband(self):
        # mismas condiciones pero SIN previsión exterior: cae al deadband de siempre,
        # que a 20.5 (> 20.0 + 0.3) ya no dispara -> idle. No regresion.
        gf = _grid(["llano", "punta", "punta", "punta", "punta"])
        action, _ = scheduler._price_anticipation_preheat(
            True, 20.5, 20.0, 0.3, "llano", gf, 1500.0, 23.0, 12.0,
            idle_loss_coeff=0.05, outdoor_forecast=None,
        )
        self.assertEqual(action, "idle")

    def test_never_crosses_safety_ceiling(self):
        # max_temp muy pegado (20.2): el pre-banqueo se capa ahi; a 20.5 ya se esta
        # por encima del techo de banqueo -> idle, nunca pide mas calor.
        gf = _grid(["llano", "punta", "punta", "punta", "punta"])
        action, _ = scheduler._price_anticipation_preheat(
            True, 20.5, 20.0, 0.3, "llano", gf, 1500.0, 20.2, 12.0,
            idle_loss_coeff=0.05, outdoor_forecast=[5.0] * 6,
        )
        self.assertEqual(action, "idle")

    def test_no_grid_forecast_is_idle(self):
        action, _ = scheduler._price_anticipation_preheat(
            True, 19.0, 20.0, 0.3, "llano", None, 1500.0, 23.0, 12.0,
            idle_loss_coeff=0.05, outdoor_forecast=[5.0] * 6,
        )
        self.assertEqual(action, "idle")

    def test_current_hour_punta_does_not_preheat(self):
        gf = _grid(["punta", "punta", "punta"])
        action, _ = scheduler._price_anticipation_preheat(
            True, 19.0, 20.0, 0.3, "punta", gf, 1500.0, 23.0, 12.0,
            idle_loss_coeff=0.05, outdoor_forecast=[5.0] * 6,
        )
        self.assertEqual(action, "idle")


if __name__ == "__main__":
    unittest.main()
