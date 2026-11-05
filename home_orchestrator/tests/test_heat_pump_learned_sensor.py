"""5-nov (decision del usuario): lo que gasta un aire MANTENIENDO se aprende
comparando el consumo de la casa al entrar/salir de mantener -- automatico, se
rehace con cada recalculo y depende del exterior -- y Energy publica con eso un
sensor "Aires (estimado)" en W y kWh."""
import os, sys, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.power_model as pm  # noqa: E402
import climate.zone_runner as zr  # noqa: E402
import climate_link  # noqa: E402
import main  # noqa: E402

NOW = datetime(2026, 11, 5, 23, 0, tzinfo=timezone.utc)


def _log_and_house(n, hold_w, base=300.0, outdoor=None):
    """n ciclos: calentar -> mantener (hold_w) -> parado; consumo de la casa
    escalonado con la potencia de cada estado."""
    log, house, outdoor_at = [], [], {}
    t = NOW - timedelta(days=2)
    for i in range(n):
        for act, w in (("heat", 1000.0), ("hold", hold_w), ("off", 0.0)):
            ts = t.timestamp()
            log.append((ts, act))
            house.append(pm._SyntheticState(str(base + w), t))
            outdoor_at[ts] = outdoor[i] if outdoor else None
            t += timedelta(minutes=40)
    return log, house, outdoor_at


def _fake_window(house):
    return lambda ws, sensor, t0, t1: [s for s in house if s.last_changed <= t1]


class LearnHold(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(pm, "_utcnow", lambda: NOW)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_learns_hold_power_from_transitions(self):
        log, house, oat = _log_and_house(5, 450.0)
        with mock.patch.object(pm, "_power_window", _fake_window(house)):
            model = pm._learn_zone_power_sync(None, log, "sensor.casa", 14, [], oat)
        self.assertEqual(pm.zone_hold_power_at(model, "heat", None), 450.0)
        self.assertEqual(model["learned_power_w"], 1000.0 - 0.0)   # arranque desde parado: hold->heat no cuenta

    def test_follows_outdoor_not_static(self):
        outdoor = [0, 2, 4, 6, 8, 10, 12]
        log, house, oat = [], [], {}
        for i, t_out in enumerate(outdoor):
            l, h, o = _log_and_house(1, 700.0 - 30 * t_out, outdoor=[t_out])
            shift = timedelta(hours=3 * i)
            log += [(ts + shift.total_seconds(), a) for ts, a in l]
            house += [pm._SyntheticState(s.state, s.last_changed + shift) for s in h]
            oat.update({ts + shift.total_seconds(): v for ts, v in o.items()})
        with mock.patch.object(pm, "_power_window", _fake_window(house)):
            model = pm._learn_zone_power_sync(None, log, "sensor.casa", 14, [], oat)
        self.assertGreater(pm.zone_hold_power_at(model, "heat", 0.0), pm.zone_hold_power_at(model, "heat", 12.0))

    def test_too_few_transitions_is_none(self):
        log, house, oat = _log_and_house(2, 450.0)
        with mock.patch.object(pm, "_power_window", _fake_window(house)):
            model = pm._learn_zone_power_sync(None, log, "sensor.casa", 14, [], oat)
        self.assertIsNone(pm.zone_hold_power_at(model, "heat", None))


class HoldEstimatePrefersLearned(unittest.TestCase):
    def test_learned_wins_over_thermal(self):
        s = type("S", (), {})()
        s._thermal_model = {"reliable": True, "heating_rate_deg_h": 0.9, "idle_loss_coeff": 0.097}
        s.current_temperature, s._outdoor_now = 21.0, 8.0
        s._zone_power = {"hold_by_mode": {"heat": {"median_w": 420.0, "samples": 5, "reliable": True}}}
        s.zone_power_for = lambda m, e: 961.0
        self.assertEqual(zr.ZoneRunner._hold_power_estimate_w(s), 420.0)


class EnergySensor(unittest.TestCase):
    def test_unmetered_only_counts_learned(self):
        e = climate_link._entry_from_state({"entity_id": "climate.d", "attributes": {
            "zone_power_w": 1001.0, "hvac_action": "heating",
            "zone_power_breakdown": {"rad": {"watts": 1.0, "source": "measured"},
                                     "zone": {"watts": 1000.0, "source": "learned_zone"}}}})
        self.assertEqual(e["unmetered_w"], 1000.0)

    def test_energy_integrates_previous_reading(self):
        sent, store = {}, {}
        def acc(key, wh):
            e = store.setdefault(key, {"since": None, "wh": 0.0})
            if wh > 0:
                e["since"] = e["since"] or "x"; e["wh"] += wh
            return dict(e)
        with mock.patch.object(main, "_publish_sensor_throttled", lambda e, s, a: sent.__setitem__(e, s)), \
                mock.patch.object(main.outlet_energy_store, "accumulate", acc), \
                mock.patch.object(main.monotonic_sensor, "publishable", lambda e, t, **k: t), \
                mock.patch.object(main, "_heat_pump_last", None):
            main._publish_heat_pump_sensors(600.0, now_ts=0.0)
            main._publish_heat_pump_sensors(0.0, now_ts=300.0)     # 5 min a 600 W
        self.assertEqual(sent["sensor.battery_orchestrator_heat_pump_power"], 0)
        self.assertAlmostEqual(store["aires"]["wh"], 50.0)


if __name__ == "__main__":
    unittest.main()
