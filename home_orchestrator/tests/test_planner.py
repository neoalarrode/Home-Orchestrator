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
        orig = tariff_source._read_pvpc_hourly_prices
        tariff_source._read_pvpc_hourly_prices = lambda e, n: hourly
        try:
            p = tariff_source.pvpc_sensor_prices("sensor.x", now, 36)
        finally:
            tariff_source._read_pvpc_hourly_prices = orig   # no dejar el parche puesto para otras pruebas
        tomorrow_night = p[12 + 2]   # 02:00 de mañana
        self.assertAlmostEqual(tomorrow_night[0], 0.05)
        self.assertEqual(tomorrow_night[1], "valle")


class SignFilteredAverage(unittest.TestCase):
    def test_expectation_not_conditional_mean(self):
        """Carga de 1000 W durante 6 min de una hora (0 W el resto) -> esperanza
        100 W (no 1000 W). La media es ponderada por TIEMPO: cada lectura vale
        hasta la siguiente."""
        from datetime import timezone
        base = datetime(2026, 9, 14, 3, 0, tzinfo=timezone.utc)
        pts = [
            {"state": "-1000", "last_changed": base.isoformat()},
            {"state": "0", "last_changed": (base + timedelta(minutes=6)).isoformat()},
            {"state": "unavailable", "last_changed": (base + timedelta(hours=1)).isoformat()},
        ]
        ha_client._safe_get_history = lambda e, d: pts
        ha_client._hourly_avg_cache.clear()
        avg, reliable = ha_client._hourly_avg_by_hour_of_day("sensor.p", 10, 0.0, False, sign_filter="negative")
        key = ha_client._bucket_key(base)
        self.assertTrue(reliable[key])
        self.assertAlmostEqual(avg[key], 100.0, places=6)
        # la mitad contraria (descarga) de ese mismo sensor: 0 W
        avg_pos, _ = ha_client._hourly_avg_by_hour_of_day("sensor.p", 10, 0.0, False, sign_filter="positive")
        self.assertAlmostEqual(avg_pos[key], 0.0, places=6)


class HistoryWindow(unittest.TestCase):
    """`/api/history/period/<inicio>` sin `end_time` devuelve UN dia, no "desde
    <inicio> hasta ahora": hay que pedir cada dia con su `end_time`."""

    def test_asks_every_day_with_end_time(self):
        calls = []

        class _R:
            def raise_for_status(self): pass
            def json(self): return [[{"state": "1", "last_changed": "2026-09-01T00:00:00+00:00"}]]

        class _S:
            def get(self, url, headers=None, params=None, timeout=None):
                calls.append((url, dict(params or {})))
                return _R()

        orig = ha_client._http
        ha_client._http = lambda: _S()
        try:
            out = ha_client.get_history("sensor.x", 10)
        finally:
            ha_client._http = orig
        self.assertEqual(len(calls), 10)
        self.assertEqual(len(out), 10)
        self.assertTrue(all("end_time" in p for _, p in calls))
        starts = [u.rsplit("/", 1)[1] for u, _ in calls]
        ends = [p["end_time"] for _, p in calls]
        self.assertEqual(starts[1:], ends[:-1])          # trozos contiguos, sin huecos ni solapes
        self.assertTrue(all("+" not in st for st in starts))  # marca limpia en la ruta

    def test_time_weighted_not_sample_count(self):
        """Muchas muestras en un rato corto no pesan mas que una sola lectura
        larga: 50 min a 200 W + 10 min a 2000 W (con 100 muestras) = 500 W."""
        from datetime import timezone
        base = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
        pts = [{"state": "200", "last_changed": base.isoformat()}]
        for i in range(100):
            pts.append({"state": "2000" if i % 2 == 0 else "2000.5",
                        "last_changed": (base + timedelta(minutes=50, seconds=6 * i)).isoformat()})
        pts.append({"state": "unknown", "last_changed": (base + timedelta(hours=1)).isoformat()})
        buckets = ha_client._weighted_buckets(pts, base + timedelta(hours=2))
        avg, reliable = buckets["raw"]
        key = ha_client._bucket_key(base)
        self.assertTrue(reliable[key])
        self.assertAlmostEqual(avg[key], 500.0, delta=1.0)

    def test_weekend_and_weekday_buckets_are_separate(self):
        from datetime import timezone
        wd = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)   # martes
        we = datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)   # sabado
        pts = [
            {"state": "300", "last_changed": wd.isoformat()},
            {"state": "unknown", "last_changed": (wd + timedelta(hours=1)).isoformat()},
            {"state": "900", "last_changed": we.isoformat()},
            {"state": "unknown", "last_changed": (we + timedelta(hours=1)).isoformat()},
        ]
        avg, _ = ha_client._weighted_buckets(pts, we + timedelta(hours=2))["raw"]
        self.assertAlmostEqual(avg[ha_client._bucket_key(wd)], 300.0)
        self.assertAlmostEqual(avg[ha_client._bucket_key(we)], 900.0)

    def test_glitch_is_dropped_and_cuts_the_hold(self):
        from datetime import timezone
        base = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
        pts = [
            {"state": "400", "last_changed": base.isoformat()},
            {"state": "55000", "last_changed": (base + timedelta(minutes=30)).isoformat()},   # glitch
            {"state": "400", "last_changed": (base + timedelta(minutes=31)).isoformat()},
            {"state": "unknown", "last_changed": (base + timedelta(hours=1)).isoformat()},
        ]
        avg, _ = ha_client._weighted_buckets(pts, base + timedelta(hours=2))["raw"]
        self.assertAlmostEqual(avg[ha_client._bucket_key(base)], 400.0)


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


class TinySolarDoesNotBlockGridCharge(unittest.TestCase):
    """Un hilo de excedente solar en una hora de valle no puede anular la
    carga desde red de esa hora (antes: 5 W de sol -> se cargaban 5 W)."""

    def _plan(self, pv0):
        now = datetime(2026, 9, 22, 6, 0)   # martes 06:00, valle hasta las 08:00
        horizon = 24
        prices = tariff_source.fixed_tariff_prices(now, horizon, tariff_source.FixedTariffConfig())
        pv = [pv0, pv0] + [0.0] * (horizon - 2)
        load = [0.0, 0.0] + [800.0] * (horizon - 2)
        plan, _ = scheduler.build_plan(now, pv, load, 1000, 9600, 4800, 4800, 288, prices)
        return plan

    def test_grid_charge_survives_a_trickle_of_solar(self):
        sin_sol = self._plan(0.0)
        con_sol = self._plan(5.0)
        self.assertGreater(sin_sol[0].charge_w, 1000)
        # con 5 W de sol tiene que cargar practicamente lo mismo, no 5 W
        self.assertGreater(con_sol[0].charge_w, sin_sol[0].charge_w - 50)
        self.assertEqual(con_sol[0].charge_source, "grid")
        self.assertLessEqual(con_sol[0].charge_w, 4800 + 1e-6)

    def test_pure_solar_hour_unchanged(self):
        """Fuera de valle/llano-con-punta-pendiente, el excedente sigue siendo solo solar."""
        now = datetime(2026, 9, 22, 12, 0)   # punta
        prices = tariff_source.fixed_tariff_prices(now, 6, tariff_source.FixedTariffConfig())
        plan, _ = scheduler.build_plan(now, [900.0] * 6, [300.0] * 6, 1000, 9600, 4800, 4800, 288, prices)
        self.assertAlmostEqual(plan[0].charge_w, 600.0)
        self.assertEqual(plan[0].charge_source, "solar")

    def test_never_exceeds_ceiling_or_max_power(self):
        now = datetime(2026, 9, 22, 6, 30)
        prices = tariff_source.fixed_tariff_prices(now, 24, tariff_source.FixedTariffConfig())
        for pv0 in (1.0, 50.0, 2000.0, 6000.0):
            plan, _ = scheduler.build_plan(now, [pv0] * 2 + [0.0] * 22, [0.0] * 2 + [900.0] * 22,
                                           9000, 9600, 4800, 4800, 288, prices, contracted_power_w=5000)
            for hp in plan:
                self.assertLessEqual(hp.soc_wh, 9600 + 1e-6)
                self.assertLessEqual(hp.charge_w, 4800 + 1e-6)


if __name__ == "__main__":
    unittest.main()
