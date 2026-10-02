"""Pruebas de los arreglos de la revision completa de Energy (stdlib unittest):
python -m unittest discover -s home_orchestrator/tests

Cada clase cubre un fallo real encontrado leyendo el plugin de principio a fin
y contrastado contra la instalacion en produccion.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

_TMP = tempfile.mkdtemp(prefix="ho_energy_tests_")
for _var, _name in (
    ("HISTORY_PATH", "history.json"), ("SAVINGS_PATH", "savings.json"), ("LIFETIME_PATH", "lifetime.json"),
    ("CAPACITY_PATH", "capacity.json"), ("GRID_ENERGY_PATH", "grid_energy.json"),
    ("MONOTONIC_SENSOR_PATH", "monotonic.json"), ("DEFERRABLE_PATH", "deferrable.json"),
    ("ANOMALY_PATH", "anomaly.json"), ("FORECAST_PATH", "forecast.json"),
    ("SOLAR_ENERGY_PATH", "solar.json"),
):
    os.environ[_var] = os.path.join(_TMP, _name)

import anomaly_store  # noqa: E402
import battery_exec  # noqa: E402
import cycle_planner  # noqa: E402
import deferrable_exec  # noqa: E402
import deferrable_store  # noqa: E402
import ecoflow_cloud  # noqa: E402
import grid_energy_store  # noqa: E402
import history_store  # noqa: E402
import json_store  # noqa: E402
import lifetime_store  # noqa: E402
import monotonic_sensor  # noqa: E402
import pv_source  # noqa: E402
import savings_store  # noqa: E402
import scheduler  # noqa: E402
import tariff_source  # noqa: E402


class JsonStore(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(_TMP, f"js_{time.time_ns()}.json")

    def test_missing_file_is_none(self):
        self.assertIsNone(json_store.load(self.path))

    def test_roundtrip_and_copy_isolation(self):
        json_store.save(self.path, {"a": [1, 2]})
        got = json_store.load(self.path)
        got["a"].append(3)                       # modificar la copia no toca la cache
        self.assertEqual(json_store.load(self.path), {"a": [1, 2]})

    def test_first_save_hits_disk_then_writes_are_deferred(self):
        json_store.save(self.path, {"n": 1})
        with open(self.path) as f:
            self.assertEqual(json.load(f), {"n": 1})      # la primera va a disco ya
        for n in range(2, 50):
            json_store.save(self.path, {"n": n})
        with open(self.path) as f:
            self.assertEqual(json.load(f), {"n": 1})      # las siguientes, diferidas...
        self.assertEqual(json_store.load(self.path), {"n": 49})   # ...pero visibles al leer
        json_store.flush_all()
        with open(self.path) as f:
            self.assertEqual(json.load(f), {"n": 49})

    def test_external_change_wins_over_cache(self):
        """Restaurar una copia de seguridad escribe el fichero por fuera."""
        json_store.save(self.path, {"n": 1})
        json_store.save(self.path, {"n": 2})              # pendiente en memoria
        time.sleep(0.02)
        with open(self.path, "w") as f:
            json.dump({"n": 999}, f)
        os.utime(self.path, ns=(time.time_ns(), time.time_ns() + 5_000_000))
        self.assertEqual(json_store.load(self.path), {"n": 999})

    def test_corrupt_file_is_none(self):
        with open(self.path, "w") as f:
            f.write("{ esto no es json")
        self.assertIsNone(json_store.load(self.path))

    def test_written_file_is_plain_indented_json(self):
        json_store.save(self.path, {"clave": "ñ"})
        raw = open(self.path, encoding="utf-8").read()
        self.assertIn('"clave": "ñ"', raw)                # mismo formato que antes


class StoresStillWork(unittest.TestCase):
    """Los almacenes pasan por json_store sin cambiar su comportamiento."""

    def test_history_record_and_today(self):
        now = datetime(2026, 10, 2, 9, 30)
        history_store.record(now - timedelta(hours=1), {"dt": "2026-10-02T08:00:00", "load_w": 500})
        history_store.record(now, {"dt": "2026-10-02T09:00:00", "load_w": 700})
        self.assertEqual([e["load_w"] for e in history_store.get_today(now)], [500])
        self.assertEqual(len([e for e in history_store.get_all() if e["dt"].startswith("2026-10-02T0")]), 2)

    def test_savings_integrates_real_elapsed_time(self):
        t0 = datetime(2026, 10, 2, 10, 0, 0)
        savings_store.record(t0, 1000, 2000, 0.2)                     # fija el punto de partida
        savings_store.record(t0 + timedelta(seconds=36), 1000, 2000, 0.2)
        s = savings_store.get_summary(t0)
        self.assertAlmostEqual(s["total_real_eur"], 0.002, places=6)
        self.assertAlmostEqual(s["total_baseline_eur"], 0.004, places=6)

    def test_grid_counters_only_add_increments(self):
        now = datetime(2026, 10, 2, 10, 0, 0)
        grid_energy_store.from_counters(100.0, 5.0, now)
        d = grid_energy_store.from_counters(100.4, 5.1, now + timedelta(minutes=5))
        self.assertAlmostEqual(d["imported_kwh"], 0.4, places=6)
        self.assertAlmostEqual(d["exported_kwh"], 0.1, places=6)

    def test_lifetime_and_monotonic(self):
        lifetime_store.accumulate("k1", "B1", charged_wh=100, discharged_wh=0)
        lifetime_store.accumulate("k1", "B1", charged_wh=50, discharged_wh=30)
        tot = lifetime_store.get_aggregate_totals(["k1"])
        self.assertEqual((tot["charged_wh"], tot["discharged_wh"]), (150, 30))
        self.assertEqual(monotonic_sensor.publishable("sensor.t", 10.0), 10.0)
        self.assertEqual(monotonic_sensor.publishable("sensor.t", 9.0), 10.0)    # nunca baja
        self.assertEqual(monotonic_sensor.publishable("sensor.t", 9.5), 10.5)


class AnomalyIsTimeBased(unittest.TestCase):
    """Antes: 3 CICLOS seguidos. Con ~2 ciclos/s eso era segundo y medio."""

    def setUp(self):
        json_store.save(anomaly_store.ANOMALY_PATH, anomaly_store._default())

    def test_spike_of_a_few_seconds_does_not_alert(self):
        t0 = datetime(2026, 10, 2, 7, 44, 0)
        for i in range(40):                                   # 20 s a 2 ciclos por segundo
            r = anomaly_store.update(t0 + timedelta(seconds=i * 0.5), 2500, 500)
        self.assertEqual(r["status"], "ok")

    def test_sustained_overconsumption_alerts_once_and_clears(self):
        t0 = datetime(2026, 10, 2, 7, 44, 0)
        changes = []
        for i in range(0, anomaly_store.CONFIRM_SECONDS + 20, 5):
            r = anomaly_store.update(t0 + timedelta(seconds=i), 2500, 500)
            if r["changed"]:
                changes.append(("on", i))
        self.assertEqual(len(changes), 1)
        self.assertGreaterEqual(changes[0][1], anomaly_store.CONFIRM_SECONDS)
        t1 = t0 + timedelta(seconds=anomaly_store.CONFIRM_SECONDS + 30)
        r = anomaly_store.update(t1, 500, 500)
        self.assertEqual(r["status"], "anomaly")              # un instante normal no la retira
        r = anomaly_store.update(t1 + timedelta(seconds=anomaly_store.CLEAR_SECONDS + 1), 500, 500)
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["changed"])

    def test_interruption_resets_the_clock(self):
        t0 = datetime(2026, 10, 2, 7, 0, 0)
        anomaly_store.update(t0, 2500, 500)
        anomaly_store.update(t0 + timedelta(seconds=100), 500, 500)       # vuelve a lo normal
        r = anomaly_store.update(t0 + timedelta(seconds=200), 2500, 500)
        self.assertEqual(r["status"], "ok")                    # el contador empezo de nuevo


class DeferrableByTime(unittest.TestCase):
    def test_seconds_without_surplus(self):
        t0 = datetime(2026, 10, 2, 12, 0, 0)
        lid = f"L{time.time_ns()}"
        self.assertEqual(deferrable_store.seconds_without_surplus(lid, True, t0), 0.0)
        self.assertEqual(deferrable_store.seconds_without_surplus(lid, False, t0), 0.0)
        self.assertAlmostEqual(deferrable_store.seconds_without_surplus(lid, False, t0 + timedelta(seconds=90)), 90.0)
        self.assertEqual(deferrable_store.seconds_without_surplus(lid, True, t0 + timedelta(seconds=95)), 0.0)
        self.assertEqual(deferrable_store.seconds_without_surplus(lid, False, t0 + timedelta(seconds=100)), 0.0)

    def test_turn_on_is_not_resent_every_cycle(self):
        calls = []
        orig_on, orig_num = deferrable_exec.ha_client.turn_on, deferrable_exec.ha_client.get_numeric_state
        deferrable_exec.ha_client.turn_on = lambda e: calls.append(e)
        deferrable_exec.ha_client.get_numeric_state = lambda e, default=None: 0.0
        deferrable_exec._last_turn_on.clear()
        try:
            t0 = datetime(2026, 10, 2, 12, 0, 0)
            load = {"id": f"D{time.time_ns()}", "name": "Lavavajillas", "switch_entity": "switch.lv", "duration_hours": 1}
            sched = {load["id"]: {"frequency": "daily", "occurrences": [
                {"start": t0.isoformat(), "end": (t0 + timedelta(hours=1)).isoformat(), "mode": "cheap", "energy_wh": 500}]}}
            for i in range(120):                                # 60 s a 2 ciclos por segundo
                deferrable_exec.execute([load], sched, t0 + timedelta(seconds=i * 0.5), 0.0, dry_run=False)
            self.assertEqual(len(calls), 1)
            deferrable_exec.execute([load], sched, t0 + timedelta(seconds=61), 0.0, dry_run=False)
            self.assertEqual(len(calls), 2)                     # reafirmado pasado el minuto
        finally:
            deferrable_exec.ha_client.turn_on, deferrable_exec.ha_client.get_numeric_state = orig_on, orig_num


class SafeStopWhenSocMissing(unittest.TestCase):
    def test_charge_is_cut_after_five_minutes_without_soc(self):
        calls = []
        orig = battery_exec.ha_client.turn_off
        battery_exec.ha_client.turn_off = lambda e: calls.append(("off", e))
        battery_exec._last_command.clear()
        battery_exec._soc_unavailable_since.clear()
        t = [datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc)]

        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return t[0]

        orig_dt = battery_exec.datetime
        battery_exec.datetime = _DT
        try:
            b = battery_exec.Battery(id="x", name="X", capacity_wh=2400, soc_sensor="s",
                                     charge_switch="switch.c", discharge_switch="switch.d")
            dist = {"action": "charge", "per_battery": [
                {"id": "x", "name": "X", "soc_pct": None, "power_w": 0, "enabled": False, "note": "sin SOC"}]}
            battery_exec.execute([b], dist, dry_run=False)
            self.assertEqual(calls, [])                         # recien caido: no se toca
            t[0] += timedelta(seconds=299)
            battery_exec.execute([b], dist, dry_run=False)
            self.assertEqual(calls, [])
            t[0] += timedelta(seconds=2)
            lines = battery_exec.execute([b], dist, dry_run=False)
            self.assertEqual(calls, [("off", "switch.c")])      # solo la CARGA
            self.assertIn("carga cortada por seguridad", lines[0])
            t[0] += timedelta(seconds=10)
            battery_exec.execute([b], dist, dry_run=False)
            self.assertEqual(len(calls), 1)                     # no se repite en cada ciclo
            # vuelve el SOC: el contador se olvida
            ok = {"action": "idle", "per_battery": [
                {"id": "x", "name": "X", "soc_pct": 50.0, "power_w": 0, "enabled": False, "note": "sin accion"}]}
            battery_exec.execute([b], ok, dry_run=True)
            self.assertNotIn("x", battery_exec._soc_unavailable_since)
        finally:
            battery_exec.datetime = orig_dt
            battery_exec.ha_client.turn_off = orig


class TariffInputIsSanitised(unittest.TestCase):
    def test_blank_or_half_typed_fields_do_not_crash(self):
        cfg = {"mode": "fixed", "punta_price": None, "llano_price": "x", "valle_price": 0.08,
               "punta_periods": [[10, 14], [18, None], ["a", "b"], [22, 20], [5]],
               "llano_periods": None, "weekend_is_valle": True}
        out = tariff_source.get_prices_tiers(cfg, datetime(2026, 9, 22, 0, 0), 24)   # martes
        self.assertEqual(len(out), 24)
        self.assertEqual(out[11], (0.173, "punta"))             # precio por defecto, periodo valido
        self.assertEqual(out[19][1], "valle")                   # periodo invalido ignorado
        self.assertEqual(out[3], (0.08, "valle"))

    def test_utc_timestamps_are_converted_to_local(self):
        base_utc = datetime(2026, 10, 1, 22, 0, tzinfo=timezone.utc)
        expected_local = base_utc.astimezone().replace(tzinfo=None)
        orig = tariff_source.ha_client.get_state
        tariff_source.ha_client.get_state = lambda e: {"attributes": {"prices": [
            {"datetime": base_utc.isoformat(), "price": 0.123}]}}
        try:
            got = tariff_source._read_pvpc_hourly_prices("sensor.p", datetime(2026, 10, 1, 12, 0))
        finally:
            tariff_source.ha_client.get_state = orig
        self.assertEqual(got, {expected_local: 0.123})


class ForecastSolarBackoff(unittest.TestCase):
    def test_failure_is_not_retried_every_cycle(self):
        calls = []

        def boom(*a):
            calls.append(a)
            raise ValueError("cuota agotada")

        orig = pv_source._fetch_raw
        pv_source._fetch_raw = boom
        pv_source._cache.clear(); pv_source._last_failure.clear()
        try:
            now = datetime(2026, 10, 2, 12, 0)
            for _ in range(200):
                out = pv_source.fetch_forecast_solar_api("arr", "", 40.9, -4.1, 30, 0, 0.8, 4, now)
            self.assertEqual(out, [0.0] * 4)
            self.assertEqual(len(calls), 1)
        finally:
            pv_source._fetch_raw = orig

    def test_changed_parameters_invalidate_the_cache(self):
        seen = []

        def fake(api_key, lat, lon, decl, az, kwp):
            seen.append(lon)
            return {"2026-10-02 12:00:00": 100.0 if lon > 0 else 300.0}

        orig = pv_source._fetch_raw
        pv_source._fetch_raw = fake
        pv_source._cache.clear(); pv_source._last_failure.clear()
        try:
            now = datetime(2026, 10, 2, 12, 0)
            a = pv_source.fetch_forecast_solar_api("arr", "", 40.9, 4.1, 30, 0, 0.8, 1, now)
            b = pv_source.fetch_forecast_solar_api("arr", "", 40.9, -4.1, 30, 0, 0.8, 1, now)
            self.assertEqual((a, b), ([100.0], [300.0]))
            self.assertEqual(seen, [4.1, -4.1])
        finally:
            pv_source._fetch_raw = orig


class EcoFlowTriggerFilter(unittest.TestCase):
    def setUp(self):
        self.c = ecoflow_cloud.EcoFlowCloudClient("k", "s")

    def test_noise_does_not_trigger(self):
        self.assertFalse(self.c._is_relevant_change("SN", {"bmsMaxCellTemp": 31, "invOutVol": 230.1}))

    def test_soc_change_triggers_once(self):
        self.assertTrue(self.c._is_relevant_change("SN", {"bmsBattSoc": 40}))
        self.assertFalse(self.c._is_relevant_change("SN", {"bmsBattSoc": 40}))
        self.assertTrue(self.c._is_relevant_change("SN", {"bmsBattSoc": 41}))

    def test_power_deadband_and_slow_drift(self):
        self.assertTrue(self.c._is_relevant_change("SN", {"powGetBpCms": 500.0}))
        self.assertFalse(self.c._is_relevant_change("SN", {"powGetBpCms": 510.0}))
        # una deriva lenta acaba disparando al acumular la banda muerta
        fired = [self.c._is_relevant_change("SN", {"powGetBpCms": 500.0 + 4 * i}) for i in range(1, 10)]
        self.assertEqual(sum(fired), 1)
        self.assertTrue(self.c._is_relevant_change("SN", {"powGetBpCms": 900.0}))


class MixedChargeWithHybridPv(unittest.TestCase):
    def test_only_the_solar_share_is_discounted(self):
        hp = scheduler.HourPlan(dt=datetime(2026, 10, 2, 7), price=0.075, tier="valle", pv_w=600, load_w=100,
                                charge_w=1500, charge_source="grid", solar_charge_w=500)
        self.assertEqual(cycle_planner.ac_charge_for_now(hp, 400), 1100)   # 400 W ya entran por los paneles
        self.assertEqual(cycle_planner.ac_charge_for_now(hp, 900), 1000)   # nunca se descuenta la parte de red
        pure = scheduler.HourPlan(dt=datetime(2026, 10, 2, 3), price=0.075, tier="valle", pv_w=0, load_w=100,
                                  charge_w=1200, charge_source="grid")
        self.assertEqual(cycle_planner.ac_charge_for_now(pure, 300), 1200)


class LlanoEmergencyChargeCountsSolar(unittest.TestCase):
    """La carga de emergencia en llano no puede ignorar el sol previsto antes de la punta."""

    def _plan(self, pv):
        now = datetime(2026, 9, 22, 14, 0)          # martes: llano 14-18, punta 18-22
        prices = tariff_source.fixed_tariff_prices(now, 8, tariff_source.FixedTariffConfig())
        load = [300.0] * 4 + [1000.0] * 4
        return scheduler.build_plan(now, pv, load, 1500, 9600, 4800, 4800, 288, prices)[0]

    def test_without_solar_it_charges_exactly_the_shortfall(self):
        plan = self._plan([0.0] * 8)
        # punta: 4 kWh; disponible 1500-288 = 1212 -> faltan 2788 Wh
        self.assertEqual(plan[0].charge_source, "grid")
        self.assertAlmostEqual(plan[0].charge_w, 2788.0, delta=1.0)

    def test_expected_solar_reduces_the_grid_charge(self):
        sin_sol = self._plan([0.0] * 8)
        con_sol = self._plan([300.0, 1800.0, 1800.0, 300.0, 0, 0, 0, 0])   # 3000 Wh de excedente a las 15-17
        esperado = 2788.0 - 3000.0 * scheduler.SOLAR_CONFIDENCE
        self.assertAlmostEqual(con_sol[0].charge_w, esperado, delta=1.0)
        self.assertLess(con_sol[0].charge_w, sin_sol[0].charge_w)

    def test_enough_solar_means_no_grid_charge_and_punta_still_covered(self):
        plan = self._plan([300.0, 3300.0, 3300.0, 300.0, 0, 0, 0, 0])
        self.assertEqual(plan[0].charge_w, 0.0)
        self.assertTrue(all(hp.discharge_w >= 999.0 for hp in plan[4:]))   # la punta se cubre entera


class LiveTotalLoadAndSurplus(unittest.TestCase):
    def setUp(self):
        import main
        self.f = main._live_total_and_surplus

    def test_two_sensor_mode(self):
        f = self.f
        self.assertEqual(f(False, 0.0, 0.0, 0.0, 450.0, True, None), (450.0, 0.0))      # la bateria cubre la casa
        self.assertEqual(f(False, 0.0, 600.0, 100.0, 0.0, True, None), (500.0, 100.0))  # sol 600, casa 500
        self.assertEqual(f(False, 800.0, 200.0, 1000.0, 0.0, True, None), (1000.0, 0.0))  # importando: sin excedente
        self.assertEqual(f(False, 0.0, 900.0, 200.0, 0.0, True, 300.0), (400.0, 500.0))  # vierte 300 y carga 200
        self.assertEqual(f(False, 300.0, None, 0.0, 100.0, True, None), (400.0, None))
        self.assertEqual(f(False, None, 500.0, 0.0, 0.0, True, None), (None, None))

    def test_unified_mode(self):
        self.assertEqual(self.f(True, 500.0, 900.0, 0.0, 0.0, True, None), (500.0, 400.0))


class HistoryIsHourlyAverage(unittest.TestCase):
    def test_last_reading_does_not_become_the_hour(self):
        t0 = datetime(2026, 10, 5, 13, 0, 0)
        fields = ("load_w",)
        # 59 minutos a 400 W y el ultimo minuto a 3000 W (horno a las 13:59),
        # con un ciclo cada 15 s como en produccion
        for k in range(0, 59 * 4):
            history_store.record(t0 + timedelta(seconds=15 * k), {"dt": t0.isoformat(), "load_w": 400}, averaged_fields=fields)
        for k in range(59 * 4, 60 * 4):
            history_store.record(t0 + timedelta(seconds=15 * k), {"dt": t0.isoformat(), "load_w": 3000}, averaged_fields=fields)
        history_store.record(t0 + timedelta(minutes=59, seconds=59), {"dt": t0.isoformat(), "load_w": 3000}, averaged_fields=fields)
        entry = [e for e in history_store.get_all() if e["dt"] == t0.isoformat()][0]
        self.assertAlmostEqual(entry["load_w"], (59 * 60 * 400 + 60 * 3000) / 3600, delta=2)
        self.assertNotIn("_avg", entry)                                # lo interno no sale

    def test_gap_is_not_filled(self):
        t0 = datetime(2026, 10, 6, 9, 0, 0)
        history_store.record(t0, {"dt": t0.isoformat(), "load_w": 5000}, averaged_fields=("load_w",))
        # addon parado 40 min: ese rato no se integra con los 5000 W de antes
        t1 = t0 + timedelta(minutes=40)
        history_store.record(t1, {"dt": t0.isoformat(), "load_w": 200}, averaged_fields=("load_w",))
        history_store.record(t1 + timedelta(minutes=5), {"dt": t0.isoformat(), "load_w": 200}, averaged_fields=("load_w",))
        entry = [e for e in history_store.get_all() if e["dt"] == t0.isoformat()][0]
        self.assertEqual(entry["load_w"], 200)


class EntityForecastAlignment(unittest.TestCase):
    def _with_state(self, attrs, fn):
        import ha_client
        orig = ha_client.get_state
        ha_client.get_state = lambda e: {"state": "0", "attributes": attrs}
        try:
            return fn(ha_client)
        finally:
            ha_client.get_state = orig

    def test_dict_keyed_by_time_is_aligned_to_now(self):
        now = datetime.now().replace(minute=0, second=0, microsecond=0)
        day_start = now.replace(hour=0)
        series = {(day_start + timedelta(hours=h)).isoformat(): float(h * 10) for h in range(48)}
        out = self._with_state({"watts": series}, lambda c: c.pv_forecast_from_entity("sensor.f", 4))
        self.assertEqual(out, [float((now.hour + i) * 10) for i in range(4)])   # no el valor de las 00:00

    def test_list_with_utc_timestamps(self):
        now_local = datetime.now().astimezone().replace(minute=0, second=0, microsecond=0)
        items = [{"datetime": (now_local + timedelta(hours=h)).astimezone(timezone.utc).isoformat(), "power": 100.0 + h}
                 for h in range(-3, 6)]
        out = self._with_state({"forecasts": items}, lambda c: c.pv_forecast_from_entity("sensor.f", 3))
        self.assertEqual(out, [100.0, 101.0, 102.0])

    def test_list_without_timestamps_stays_positional(self):
        items = [{"p_pv_forecast": v} for v in (0, 0, 50, 0)]            # ceros de noche incluidos
        out = self._with_state({"forecasts": items}, lambda c: c.pv_forecast_from_entity("sensor.f", 6))
        self.assertEqual(out, [0.0, 0.0, 50.0, 0.0, 0.0, 0.0])


class DeferrableWindowNotAtEndOfHour(unittest.TestCase):
    def test_current_hour_is_skipped_when_almost_over(self):
        import deferrable_scheduler as ds
        self.assertEqual(ds._first_usable_hour(datetime(2026, 10, 2, 7, 5)), 0)
        self.assertEqual(ds._first_usable_hour(datetime(2026, 10, 2, 7, 58)), 1)
        now = datetime(2026, 10, 2, 7, 58)
        hours = [now.replace(minute=0) + timedelta(hours=i) for i in range(24)]
        load = {"id": f"W{time.time_ns()}", "name": "Lavadora", "duration_hours": 1, "frequency": "daily",
                "estimated_energy_wh": 800}
        sched = ds.plan_for_load(load, now, hours, [0.0] * 24, [300.0] * 24, [0.0] * 24, [None] * 24, [0.1] * 24)
        self.assertEqual(sched["occurrences"][0]["start"], hours[1].isoformat())   # 08:00, no 07:00


class EcoFlowStartBackoff(unittest.TestCase):
    def test_failed_start_is_not_retried_on_every_read(self):
        calls = []

        class _C:
            _started = False
            _last_start_failure = 0.0

            def start(self):
                calls.append(1)
                return False

        key = ("k-test", f"s-{time.time_ns()}")
        ecoflow_cloud._clients[key] = _C()
        try:
            for _ in range(50):
                ecoflow_cloud.get_client(*key)
            self.assertEqual(len(calls), 1)
        finally:
            ecoflow_cloud._clients.pop(key, None)


class _FakeEcoFlow(battery_exec.Battery):
    """Bateria EcoFlow que apunta lo que se le manda a un "equipo" compartido
    por grupo: interruptores de tarea y limite de descarga comunes, limite de
    carga por unidad -- igual que las STREAM enlazadas de verdad."""
    device: dict = None

    def ecoflow_set_charging_task(self, enable=None, power_limit_w=None, target_soc=None):
        if enable is not None:
            self.device["charge_enabled"] = enable
        if power_limit_w is not None:
            self.device["charge_limit"][self.id] = power_limit_w
        return True

    def ecoflow_set_discharging_task(self, enable=None, power_limit_w=None):
        if enable is not None:
            self.device["discharge_enabled"] = enable
        if power_limit_w is not None:
            self.device["discharge_limit"] = power_limit_w
        return True


class EcoFlowGroupCommands(unittest.TestCase):
    """Las unidades de un grupo EcoFlow comparten la tarea de carga y el limite
    de descarga: una unidad sin nada que hacer no puede apagar a las demas, sea
    cual sea su posicion en la lista."""

    def _run(self, action, entries, order):
        device = {"charge_enabled": None, "discharge_enabled": None, "discharge_limit": None, "charge_limit": {}}
        bats = []
        for bid in order:
            b = _FakeEcoFlow(id=bid, name=bid.upper(), capacity_wh=2400, source="ecoflow",
                             ecoflow_mode="hybrid", ecoflow_sn=bid, ecoflow_main_sn="MAIN")
            b.device = device
            bats.append(b)
        dist = {"action": action, "per_battery": [dict(e) for bid in order for e in entries if e["id"] == bid]}
        battery_exec._last_command.clear()
        battery_exec._soc_unavailable_since.clear()
        lines = battery_exec.execute(bats, dist, dry_run=False)
        return device, lines

    def test_full_unit_does_not_stop_the_group_from_charging(self):
        entries = [
            {"id": "a", "name": "A", "soc_pct": 90.0, "power_w": 600, "enabled": True, "note": "reparto por capacidad"},
            {"id": "b", "name": "B", "soc_pct": 100.0, "power_w": 0, "enabled": False, "note": "reparto por capacidad"},
            {"id": "c", "name": "C", "soc_pct": 95.0, "power_w": 300, "enabled": True, "note": "reparto por capacidad"},
        ]
        for order in (["a", "b", "c"], ["a", "c", "b"], ["b", "a", "c"]):   # la llena en medio, al final, al principio
            device, _ = self._run("charge", entries, order)
            self.assertTrue(device["charge_enabled"], order)
            self.assertFalse(device["discharge_enabled"], order)
            self.assertEqual(device["charge_limit"], {"a": 600, "b": 0, "c": 300}, order)

    def test_unit_at_minimum_does_not_zero_the_group_discharge(self):
        entries = [
            {"id": "a", "name": "A", "soc_pct": 40.0, "power_w": 1200, "enabled": True, "note": "limite al maximo declarado"},
            {"id": "b", "name": "B", "soc_pct": 3.0, "power_w": 0, "enabled": False, "note": "sin margen (al minimo)"},
        ]
        for order in (["a", "b"], ["b", "a"]):
            device, lines = self._run("discharge", entries, order)
            self.assertTrue(device["discharge_enabled"], order)
            self.assertEqual(device["discharge_limit"], 1200, order)
            self.assertFalse(device["charge_enabled"], order)
            self.assertTrue(any("limite comun 1200 W" in ln for ln in lines))

    def test_nobody_can_discharge_blocks_the_group(self):
        entries = [
            {"id": "a", "name": "A", "soc_pct": 3.0, "power_w": 0, "enabled": False, "note": "sin margen (al minimo)"},
            {"id": "b", "name": "B", "soc_pct": 3.0, "power_w": 0, "enabled": False, "note": "sin margen (al minimo)"},
        ]
        device, _ = self._run("discharge", entries, ["a", "b"])
        self.assertEqual(device["discharge_limit"], 0)
        self.assertFalse(device["charge_enabled"])

    def test_idle_is_unchanged(self):
        entries = [{"id": x, "name": x, "soc_pct": 50.0, "power_w": 0, "enabled": False, "note": "sin accion"} for x in "ab"]
        device, _ = self._run("idle", entries, ["a", "b"])
        self.assertFalse(device["charge_enabled"])
        self.assertTrue(device["discharge_enabled"])
        self.assertEqual(device["discharge_limit"], 0)

    def test_unit_without_soc_is_left_out_of_the_group_decision(self):
        entries = [
            {"id": "a", "name": "A", "soc_pct": None, "power_w": 0, "enabled": False, "note": "sin SOC"},
            {"id": "b", "name": "B", "soc_pct": 50.0, "power_w": 500, "enabled": True, "note": "reparto por capacidad"},
        ]
        device, _ = self._run("charge", entries, ["a", "b"])
        self.assertTrue(device["charge_enabled"])
        self.assertEqual(device["charge_limit"], {"b": 500})      # a "a" no se le manda nada

    def test_batteries_without_group_keep_their_own_commands(self):
        calls = []
        ha = battery_exec.ha_client
        orig = (ha.turn_on, ha.turn_off, ha.set_number)
        ha.turn_on = lambda e: calls.append(("on", e))
        ha.turn_off = lambda e: calls.append(("off", e))
        ha.set_number = lambda e, v: calls.append(("num", e, v))
        try:
            bats = [battery_exec.Battery(id=x, name=x, capacity_wh=2400, soc_sensor="s", charge_switch=f"switch.c{x}",
                                         discharge_switch=f"switch.d{x}") for x in "ab"]
            dist = {"action": "charge", "per_battery": [
                {"id": "a", "name": "a", "soc_pct": 50.0, "power_w": 600, "enabled": True, "note": "n"},
                {"id": "b", "name": "b", "soc_pct": 100.0, "power_w": 0, "enabled": False, "note": "n"}]}
            battery_exec._last_command.clear()
            battery_exec.execute(bats, dist, dry_run=False)
        finally:
            ha.turn_on, ha.turn_off, ha.set_number = orig
        self.assertIn(("on", "switch.ca"), calls)
        self.assertIn(("off", "switch.cb"), calls)                # la llena apaga SU switch, como siempre


class ConfigSaveKeepsServerSideFields(unittest.TestCase):
    """Guardar el formulario no puede pisar lo que el backend escribio despues
    de que la pagina cargase su copia de la configuracion."""

    def setUp(self):
        import config_store
        self.config_store = config_store
        self._orig_path = config_store.CONFIG_PATH
        config_store.CONFIG_PATH = os.path.join(tempfile.mkdtemp(prefix="ho_cfg_"), "config.json")
        import main
        self.main = main

    def tearDown(self):
        self.config_store.CONFIG_PATH = self._orig_path

    def test_stale_page_copy_does_not_undo_backend_changes(self):
        cs = self.config_store
        cfg = cs.load_config()
        load = cs.add_deferrable_load(cfg, {"name": "Lavavajillas", "switch": "switch.x", "frequency": "once"})
        bat = cs.add_battery(cfg, {"name": "B1", "capacity_wh": 2400, "source": "ecoflow"})
        page_copy = json.loads(json.dumps(cs.load_config()))          # lo que cargo la pagina

        # el backend trabaja mientras la pagina sigue abierta
        cs.update_deferrable_load(cfg, load["id"], {"done": True})
        cs.update_battery(cfg, bat["id"], {"ecoflow_ble_address": "AA:BB"})
        self.main._update_config(lambda fresh: fresh.__setitem__("grafana_last_sync", "2026-10-02T10:00:00"))

        # el usuario cambia el idioma y la tarifa y guarda
        page_copy["general"]["language"] = "en"
        page_copy["tariff"]["valle_price"] = 0.05
        with self.main.app.test_request_context("/api/config", method="POST", json=page_copy):
            r = self.main.api_save_config()
        self.assertEqual(r.status_code, 200)

        saved = cs.load_config()
        self.assertEqual(saved["general"]["language"], "en")
        self.assertEqual(saved["tariff"]["valle_price"], 0.05)
        self.assertTrue(saved["deferrable_loads"][0]["done"])
        self.assertEqual(saved["batteries"][0]["ecoflow_ble_address"], "AA:BB")
        self.assertEqual(saved["grafana_last_sync"], "2026-10-02T10:00:00")
        self.assertEqual(r.get_json()["batteries"][0]["ecoflow_ble_address"], "AA:BB")

    def test_rejects_something_that_is_not_a_config(self):
        with self.main.app.test_request_context("/api/config", method="POST", json=["no", "es", "config"]):
            _, status = self.main.api_save_config()
        self.assertEqual(status, 400)


class PluginFlushesOnShutdown(unittest.TestCase):
    def test_pending_data_reaches_disk(self):
        import battery_plugin
        path = os.path.join(tempfile.mkdtemp(prefix="ho_flush_"), "x.json")
        json_store.save(path, {"v": 1})          # primera escritura: va a disco
        json_store.save(path, {"v": 2})          # segunda: se queda en memoria
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"v": 1})
        battery_plugin.BatteryPlugin().shutdown()
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"v": 2})


class DpPlannerIsOptimal(unittest.TestCase):
    """El motor "dp" contra una busqueda exhaustiva sobre una malla fina."""

    ETA = 0.94

    def _stages(self, now, pv, load, prices, mc, md, contracted):
        frac = 1 - (now.minute * 60) / 3600
        out = []
        for i in range(len(pv)):
            dt = frac if i == 0 else 1.0
            p = prices[i][0]
            e = min(0.04, p)
            i0 = (load[i] - pv[i]) * dt
            d_min = 0.0 if p < 0 else -min(md * dt, max(0, i0)) / self.ETA
            emax = mc * dt
            es = min(max(0, -i0), emax)
            gc = max(0, contracted - load[i]) if contracted > 0 else mc
            eg = max(0, min(emax - es, gc * dt))
            out.append((i0, p, e, d_min, (es + eg) * self.ETA))
        return out

    def _cost(self, st, delta):
        i0, p, e, d_min, d_max = st
        if delta < d_min - 1e-6 or delta > d_max + 1e-6:
            return None
        if delta <= 0:
            g = i0 + delta * self.ETA
            return (p * g if g > 0 else e * g) + 0.01 * (-delta)
        g = i0 + delta / self.ETA
        return p * g if g > 0 else e * g

    def test_matches_exhaustive_search(self):
        import random
        import scheduler_dp
        lo, hi = 288.0, 9600.0
        for seed in (9, 17, 29, 47):                 # 29 y 47: los que fallaban antes del arreglo
            r = random.Random(seed)
            n = r.choice([6, 12, 24])
            mc, md = r.choice([1200, 4800]), r.choice([1200, 4800])
            pv = [max(0.0, r.gauss(300, 500)) for _ in range(n)]
            load = [max(0.0, r.gauss(700, 500)) for _ in range(n)]
            prices = [(round(r.uniform(0.03, 0.30), 3), "llano") for _ in range(n)]
            contracted = r.choice([0, 5000])
            soc0 = r.uniform(lo, hi)
            now = datetime(2026, 9, 22, 10, r.choice([0, 0, 20, 45]))
            plan, _ = scheduler_dp.build_plan_dp(now, pv, load, soc0, hi, mc, md, lo, prices, contracted_power_w=contracted)
            st = self._stages(now, pv, load, prices, mc, md, contracted)
            wts = [max(0, load[i] - pv[i]) for i in range(n)]
            vt = max(0.0, self.ETA * sum(prices[i][0] * wts[i] for i in range(n)) / sum(wts) - 0.01)

            s, plan_cost = soc0, 0.0
            for i, hp in enumerate(plan):
                c = self._cost(st[i], hp.soc_wh - s)
                self.assertIsNotNone(c, f"paso inviable (semilla {seed}, hora {i})")
                self.assertTrue(lo - 1e-6 <= hp.soc_wh <= hi + 1e-6)
                plan_cost += c
                s = hp.soc_wh
            plan_cost -= vt * (s - lo)

            levels = 240
            step = (hi - lo) / levels
            value = [-vt * (k * step) for k in range(levels + 1)]
            for i in range(n - 1, -1, -1):
                down, up = int(-st[i][3] / step), int(st[i][4] / step)
                value = [min(self._cost(st[i], m * step) + value[k + m]
                             for m in range(max(-down, -k), min(up, levels - k) + 1))
                         for k in range(levels + 1)]
            x = (soc0 - lo) / step
            k = min(levels - 1, int(x))
            best = value[k] * (1 - (x - k)) + value[k + 1] * (x - k)
            # precios en EUR/kWh x Wh => milesimas de euro; se tolera 1 centimo
            self.assertLessEqual(plan_cost, best + 10.0, f"semilla {seed}: {plan_cost:.1f} frente a {best:.1f}")

    def test_solar_surplus_is_stored_when_it_pays_off(self):
        """Antes el excedente sin tocar la bateria se valoraba a precio de compra
        y el motor preferia verterlo."""
        import scheduler_dp
        now = datetime(2026, 9, 22, 12, 0)
        # comprar a 0,29 para gastar a 0,30 no compensa (perdidas): solo sol
        prices = [(0.29, "llano")] * 3 + [(0.30, "punta")] * 3 + [(0.08, "valle")] * 3
        pv = [1500.0] * 3 + [0.0] * 6
        load = [300.0] * 3 + [900.0] * 6
        plan, _ = scheduler_dp.build_plan_dp(now, pv, load, 1000, 9600, 4800, 4800, 288, prices)
        self.assertGreater(plan[0].charge_w, 1000)
        self.assertEqual(plan[0].charge_source, "solar")
        self.assertGreater(plan[3].discharge_w, 800)


if __name__ == "__main__":
    unittest.main()
