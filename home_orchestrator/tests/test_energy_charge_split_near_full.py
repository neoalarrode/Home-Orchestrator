"""El reparto de la carga no limita la potencia de una bateria a los Wh que le
faltan (3-nov: a las 07:48 la Max Patio, a 134 Wh de llenarse, solo podia cargar
134 W con 12 min de valle por delante; acabaron el valle al 93-94 %)."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import battery_exec as be  # noqa: E402


def _bat(i, cap):
    return be.Battery(id=i, name=i, capacity_wh=cap, source="ecoflow", ecoflow_mode="hybrid",
                      max_charge_w=1200, max_soc_pct=100)


class NearFull(unittest.TestCase):
    def test_power_follows_the_plan_not_the_missing_wh(self):
        bats = [_bat("mp", 1920), _bat("ux", 3840)]
        socs = {"mp": 93.0, "ux": 93.0}
        d = be.plan_distribution(bats, charge_w=1500, discharge_w=0, socs=socs)
        p = {e["id"]: e["power_w"] for e in d["per_battery"]}
        self.assertEqual(sum(p.values()), 1500)
        self.assertGreater(p["mp"], 134)                 # antes: 134 W como mucho
        self.assertAlmostEqual(p["ux"], 2 * p["mp"], delta=2)   # sigue repartiendo por hueco

    def test_full_battery_still_gets_nothing(self):
        bats = [_bat("mp", 1920), _bat("ux", 3840)]
        d = be.plan_distribution(bats, charge_w=1000, discharge_w=0, socs={"mp": 100.0, "ux": 50.0})
        p = {e["id"]: e["power_w"] for e in d["per_battery"]}
        self.assertEqual(p["mp"], 0)
        self.assertEqual(p["ux"], 1000)


if __name__ == "__main__":
    unittest.main()
