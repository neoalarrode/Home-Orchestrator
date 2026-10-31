"""Potencia de zona aprendida de sus arranques (registro de acciones de Climate)
+ consumo total de la casa, para delegados de puente sin historico en HA; y su
uso en confort para aprovechar el excedente solar dentro de la banda."""
import os, sys, time, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.power_model as pm  # noqa: E402
import climate.zone_runner as zr  # noqa: E402
from climate import scheduler  # noqa: E402

NOW = time.time()


class _WS:
    """Consumo de la casa: base 600 W + la zona (800 W) mientras calienta, con
    un punto cada 20 s, y otra carga de 2000 W en el arranque contaminado.
    Responde a history/history_during_period como HA (formato s/lu)."""
    def __init__(self, runs, noisy_at=None):
        self.runs, self.noisy_at = runs, noisy_at
        self.calls = 0

    def _w(self, t):
        w = 600.0 + sum(800.0 for a, b in self.runs if a <= t < b)
        if self.noisy_at and self.noisy_at <= t < self.noisy_at + 600:
            w += 2000.0
        return w

    def call(self, msg_type, **kw):
        from datetime import datetime
        self.calls += 1
        t0 = datetime.fromisoformat(kw["start_time"]).timestamp()
        t1 = datetime.fromisoformat(kw["end_time"]).timestamp()
        pts, t = [{"s": str(self._w(t0)), "lu": t0}], t0 + 20
        while t <= t1:
            pts.append({"s": str(self._w(t)), "lu": t}); t += 20
        return {kw["entity_ids"][0]: pts}


def _log(starts, minutes=40):
    log, runs = [], []
    for st in starts:
        log += [(st, "heat"), (st + minutes * 60, "off")]
        runs.append((st, st + minutes * 60))
    return log, runs


class ZonePowerLearning(unittest.TestCase):
    def test_learns_zone_power_from_starts(self):
        starts = [NOW - h * 3600 for h in (50, 40, 30, 20, 10)]
        log, runs = _log(starts)
        m = pm._learn_zone_power_sync(_WS(runs), log, "sensor.casa", 5, [])
        self.assertTrue(m["reliable"])
        self.assertAlmostEqual(m["learned_power_w"], 800, delta=30)

    def test_other_zone_start_is_discarded(self):
        starts = [NOW - h * 3600 for h in (50, 40, 30, 20, 10)]
        log, runs = _log(starts)
        m = pm._learn_zone_power_sync(_WS(runs), log, "sensor.casa", 5, [starts[0] + 60])
        self.assertEqual(m["samples_discarded_other_zone"], 1)
        self.assertEqual(m["samples_used"], 4)

    def test_median_resists_a_coincident_load(self):
        starts = [NOW - h * 3600 for h in (50, 40, 30, 20, 10)]
        log, runs = _log(starts)
        m = pm._learn_zone_power_sync(_WS(runs, noisy_at=starts[2]), log, "sensor.casa", 5, [])
        self.assertAlmostEqual(m["learned_power_w"], 800, delta=30)

    def test_only_small_windows_are_queried(self):
        starts = [NOW - h * 3600 for h in range(60, 0, -1)]   # 60 arranques
        log, runs = _log(starts, minutes=20)
        ws = _WS(runs)
        pm._learn_zone_power_sync(ws, log, "sensor.casa", 5, [])
        self.assertEqual(ws.calls, pm.ZONE_MAX_SAMPLES)          # solo los 30 mas recientes

    def test_older_starts_than_five_days_still_count(self):
        starts = [NOW - d * 86400 for d in (12, 10, 9, 8, 7)]  # salon: sin arrancar en 5 dias
        log, runs = _log(starts)
        self.assertTrue(pm._learn_zone_power_sync(_WS(runs), log, "sensor.casa", 5, [])["reliable"])

    def test_too_few_starts_not_reliable(self):
        log, runs = _log([NOW - 7200, NOW - 3600])
        self.assertFalse(pm._learn_zone_power_sync(_WS(runs), log, "sensor.casa", 5, [])["reliable"])


class _PowerStub:
    def __init__(self, zone_power):
        self.zone = {"actuator_power": {}}
        self._power_model = {}
        self._zone_power = zone_power
        self.est = zr.ZoneRunner._zone_estimated_power_w.__get__(self)
        self._actuator_power_w = zr.ZoneRunner._actuator_power_w.__get__(self)
    def _climate_actuators(self): return ["tuya:a"]
    def _get_state(self, e): return None


class ZoneEstimatedPower(unittest.TestCase):
    def test_uses_learned_zone_power(self):
        self.assertEqual(_PowerStub({"reliable": True, "learned_power_w": 780}).est(), 780.0)

    def test_unknown_without_reliable_learning(self):
        self.assertIsNone(_PowerStub({"reliable": False, "learned_power_w": None}).est())


def _decide(surplus, temp=21.1):
    return scheduler.decide_action(
        current_temp=temp, heat_target=21.0, cool_target=25.0, priority="confort", deadband=0.3,
        min_temp=16.0, max_temp=30.0, outdoor_now=12.0, outdoor_forecast=[12.0, 12.0, 12.0],
        heating_rate_deg_h=0.9, cooling_rate_deg_h=0.0, idle_loss_coeff=None,
        grid_tier="llano", solar_surplus_now_w=surplus, battery_discharge_headroom_now_w=4800,
        zone_estimated_power_w=800, grid_forecast=None,
        occupancy_now_likely=None, occupancy_forecast_likely=None,
    )


class SolarInConfort(unittest.TestCase):
    def test_free_sun_preheats_within_band(self):
        action, reason = _decide(surplus=1500)
        self.assertEqual(action, "heat"); self.assertIn("excedente solar", reason)

    def test_not_enough_sun_stays_idle(self):
        self.assertEqual(_decide(surplus=300)[0], "idle")

    def test_never_beyond_band(self):
        self.assertEqual(_decide(surplus=1500, temp=21.4)[0], "idle")   # ya por encima de 21+0.3

    def test_battery_alone_is_not_free_sun(self):
        self.assertEqual(_decide(surplus=0)[0], "idle")


if __name__ == "__main__":
    unittest.main()
