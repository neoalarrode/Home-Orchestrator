"""6-nov: la carga sostenida ya no reparte la carga en todo el valle a poca
potencia (mal rendimiento del cargador, horas al 100 %): espera y carga a una
potencia eficiente, sin cortes, terminando antes de que la bateria haga falta."""
import os, sys, unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import scheduler  # noqa: E402
from tariff_source import FixedTariffConfig, fixed_tariff_prices  # noqa: E402


def _plan(now, soc):
    pt = fixed_tariff_prices(now, 24, FixedTariffConfig())
    plan, _ = scheduler.build_plan(now=now, pv_forecast_w=[0] * 24, load_forecast_w=[900] * 24,
                                   current_soc_wh=soc * 9600, total_capacity_wh=9600, max_charge_w=4800,
                                   max_discharge_w=4800, min_soc_wh=0.08 * 9600, prices_tiers=pt,
                                   contracted_power_w=5000, paced_charging=True)
    return plan, pt


class EfficientPaced(unittest.TestCase):
    def test_waits_then_charges_continuously_and_finishes_early(self):
        plan, pt = _plan(datetime(2026, 11, 6, 0, 45), 0.11)
        valle = [hp for hp, (_, tier) in zip(plan, pt) if tier == "valle"]
        charging = [i for i, hp in enumerate(valle) if hp.charge_w > 0]
        self.assertGreater(charging[0], 0)                                   # no empieza a las 00:45
        self.assertEqual(charging, list(range(charging[0], charging[-1] + 1)))  # sin cortes
        self.assertLess(charging[-1], len(valle) - 1)                        # acaba antes de la ultima hora de valle
        self.assertTrue(all(valle[i].charge_w >= 4800 * scheduler.PACED_EFFICIENT_FRACTION * 0.8 for i in charging[:-1]))
        self.assertAlmostEqual(valle[charging[-1]].soc_wh, 9600, delta=1)
        self.assertIn("esperando", valle[0].reason)

    def test_late_start_still_reaches_full(self):
        plan, pt = _plan(datetime(2026, 11, 6, 5, 30), 0.11)
        valle = [hp for hp, (_, tier) in zip(plan, pt) if tier == "valle"]
        self.assertGreater(valle[0].charge_w, 0)
        self.assertGreaterEqual(max(hp.soc_wh for hp in valle), 9600 * 0.7)


if __name__ == "__main__":
    unittest.main()
