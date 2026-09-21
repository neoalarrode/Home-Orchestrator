"""Pruebas del planificador (stdlib unittest): python -m unittest discover -s home_orchestrator/tests"""
import os, sys, unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import battery_exec, scheduler, tariff_source, scheduler_dp, ha_client  # noqa: E402


def _bats(n=4, cap=2400, mx=1200):
    return [battery_exec.Battery(id=f"b{i}", name=f"B{i}", capacity_wh=cap, soc_sensor="s", max_charge_w=mx) for i in range(n)]


class RoundPreservingSum(unittest.TestCase):
    def test_saturated_batteries_never_exceed_their_limit(self):
        bats = _bats()
        d = battery_exec.plan_distribution(bats, 6000, 0, socs={b.id: 50.0 for b in bats})
        self.assertEqual([e["power_w"] for e in d["per_battery"]], [1200] * 4)

    def test_sum_never_exceeds_request(self):
        for total in (0.4, 1.6, 3.0, 1000.4, 4799.9):
            bats = _bats()
            d = battery_exec.plan_distribution(bats, total, 0, socs={b.id: 50.0 for b in bats})
            self.assertLessEqual(sum(e["power_w"] for e in d["per_battery"]), total + 1e-9)


class CommandChatter(unittest.TestCase):
    def test_same_or_near_command_not_resent_every_cycle(self):
        from datetime import timezone
        calls = []
        ha = battery_exec.ha_client
        ha.turn_on = lambda e: calls.append(("on", e)); ha.turn_off = lambda e: calls.append(("off", e))
        ha.set_number = lambda e, v: calls.append(("num", e, v))
        b = battery_exec.Battery(id="x", name="X", capacity_wh=2400, soc_sensor="s", charge_switch="switch.c",
                                 discharge_switch="switch.d", charge_power_limit_entity="number.c")
        battery_exec._last_command.clear()
        t = [datetime(2026, 9, 22, 3, 0, tzinfo=timezone.utc)]

        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return t[0]
        battery_exec.datetime = _DT

        def cycle(power):
            d = {"action": "charge", "per_battery": [{"id": "x", "name": "X", "soc_pct": 50.0, "power_w": power,
                                                      "enabled": True, "note": "n"}]}
            n0 = len(calls)
            battery_exec.execute([b], d, dry_run=False)
            t[0] += timedelta(seconds=60)
            return len(calls) - n0
        try:
            self.assertGreater(cycle(336), 0)      # primera vez: se envia
            self.assertEqual(cycle(335), 0)        # 1 W de diferencia a los 60 s: no
            self.assertGreater(cycle(334), 0)      # re-asercion a los 120 s (COMMAND_REFRESH_SECONDS)
            self.assertGreater(cycle(500), 0)      # cambio real: si
            self.assertEqual(cycle(500), 0)        # misma orden a los 60 s: no
            self.assertGreater(cycle(500), 0)      # re-asercion pasados 120 s
            # una REDUCCION de 50 W (p.ej. recorte por potencia contratada) siempre se reenvia
            self.assertGreater(cycle(450), 0)
            # tras un fallo de envio la orden se olvida y se reintenta al ciclo siguiente
            orig = ha.set_number
            ha.set_number = lambda e, v: (_ for _ in ()).throw(RuntimeError("boom"))
            cycle(700)
            ha.set_number = orig
            self.assertGreater(cycle(700), 0)
        finally:
            battery_exec.datetime = datetime


class PacedChargeFractionalHour(unittest.TestCase):
    def _reach(self, minute):
        """SOC alcanzado al final de la ultima hora valle si se simula minuto a minuto (cada minuto se aplica la orden del plan)."""
        prices = [(0.075, "valle")] * 8 + [(0.173, "punta")] * 40
        load = [1000.0] * 48
        pv = [0.0] * 48
        soc, cap = 1000.0, 9600.0
        now0 = datetime(2026, 9, 22, 0, 0)
        for m in range(0, 7 * 60 + 60):
            now = now0 + timedelta(minutes=m)
            hrs_left = 8 - (m // 60)
            plan, _ = scheduler.build_plan(now, pv[:hrs_left + 40], load[:hrs_left + 40], soc, cap, 4800, 4800, 288,
                                           [(0.075, "valle")] * hrs_left + [(0.173, "punta")] * 40, paced_charging=True)
            soc += min(plan[0].charge_w, 4800) / 60
        return soc

    def test_reaches_reserve_before_first_need(self):
        self.assertGreater(self._reach(0), 9600 * 0.985)


class PvpcFallback(unittest.TestCase):
    def test_tomorrow_uses_same_hour_of_today_not_flat_mean(self):
        now = datetime(2026, 9, 22, 12, 0)
        today = datetime(2026, 9, 22)
        hourly = {today + timedelta(hours=h): (0.05 if h < 8 else 0.20) for h in range(24)}
        tariff_source._read_pvpc_hourly_prices = lambda e, n: hourly
        p = tariff_source.pvpc_sensor_prices("sensor.x", now, 36)
        tomorrow_night = p[12 + 2]   # 02:00 de mañana
        self.assertAlmostEqual(tomorrow_night[0], 0.05)
        self.assertEqual(tomorrow_night[1], "valle")


class SignFilteredAverage(unittest.TestCase):
    def test_expectation_not_conditional_mean(self):
        """Carga de 1000 W en 1 de 10 muestras de la misma hora -> esperanza 100 W (no 1000 W)."""
        from datetime import timezone
        pts = []
        for k in range(10):
            ts = datetime(2026, 9, 14 + (k % 3), 3, k, tzinfo=timezone.utc)   # mismo cubo (lunes-miercoles, 05:xx local)
            pts.append({"state": "-1000" if k == 0 else "0", "last_changed": ts.isoformat()})
        ha_client._safe_get_history = lambda e, d: pts
        ha_client._hourly_avg_cache.clear()
        avg, _ = ha_client._hourly_avg_by_hour_of_day("sensor.p", 10, 0.0, False, sign_filter="negative")
        vals = [v for v in avg.values() if v]
        self.assertTrue(any(abs(v - 100.0) < 1e-6 for v in vals), vals[:3])


class DpPlanner(unittest.TestCase):
    def test_charges_in_valle_discharges_in_punta_and_respects_limits(self):
        now = datetime(2026, 9, 22, 18, 0)
        prices = tariff_source.fixed_tariff_prices(now, 48, tariff_source.FixedTariffConfig())
        plan, _ = scheduler_dp.build_plan_dp(now, [0.0] * 48, [800.0] * 48, 4800, 9600, 4800, 4800, 288, prices)
        for hp in plan:
            self.assertGreaterEqual(hp.soc_wh, 288 - 1e-6)
            self.assertLessEqual(hp.soc_wh, 9600 + 1e-6)
            self.assertLessEqual(hp.charge_w, 4800 + 1e-6)
            self.assertLessEqual(hp.discharge_w, 800 + 1e-6)
            if hp.tier == "valle":
                self.assertEqual(hp.discharge_w, 0)
            if hp.charge_w > 0:
                self.assertNotEqual(hp.tier, "punta")

    def test_degenerate_inputs(self):
        now = datetime(2026, 9, 22, 18, 0)
        self.assertEqual(scheduler_dp.build_plan_dp(now, [], [], 0, 0, 0, 0, 0, [])[0], [])
        pr = [(0.1, "llano")] * 4
        plan, _ = scheduler_dp.build_plan_dp(now, [0] * 4, [0] * 4, 500, 1000, 0, 0, 500, pr, max_usable_wh=500)
        self.assertEqual(len(plan), 4)
        plan, _ = scheduler_dp.build_plan_dp(now, [0] * 4, [500] * 4, 500, 1000, 100, 100, 100, [(-0.05, "valle")] * 4)
        self.assertEqual(len(plan), 4)


if __name__ == "__main__":
    unittest.main()
