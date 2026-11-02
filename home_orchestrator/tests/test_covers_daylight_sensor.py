"""Dia/noche por sensor de luz (como las automatizaciones del usuario: patio
>20 lx / <20 lx durante 1 min) en vez de por la posicion del sol."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import covers.zone_runner as zr  # noqa: E402

LUX = "sensor.patio_lux"


class _WS:
    def __init__(self): self.calls = []
    def call_service(self, domain, service, service_data=None, target=None):
        self.calls.append((service, (service_data or {}).get("position"), target["entity_id"]))


def _states(lux, sun_up=True, pos=100):
    return {"sun.sun": {"state": "above_horizon" if sun_up else "below_horizon",
                        "attributes": {"elevation": 5 if sun_up else -5, "azimuth": 250}},
            LUX: {"state": str(lux)},
            "cover.c": {"state": "open", "attributes": {"current_position": pos, "supported_features": 15}}}


def _zone(**extra):
    ws = _WS()
    cfg = {"cover_entities": ["cover.c"], "night_close_enabled": True, "day_open_enabled": True,
           "daylight_sensor": LUX, "daylight_lux_threshold": 20, "daylight_lux_for_seconds": 60}
    cfg.update(extra)
    return zr.ZoneRunner("z", cfg, ws), ws


class DaylightSensor(unittest.TestCase):
    def test_watches_the_sensor(self):
        z, _ = _zone()
        self.assertIn(LUX, z.watched_entities())

    def test_dark_needs_a_full_minute(self):
        z, _ = _zone()
        self.assertTrue(z._is_daytime(_states(3000), {"state": "above_horizon"}, now=0))
        # Baja la luz con el sol aun arriba: no cambia hasta pasar 60 s seguidos.
        self.assertTrue(z._is_daytime(_states(15), {"state": "above_horizon"}, now=10))
        self.assertTrue(z._is_daytime(_states(15), {"state": "above_horizon"}, now=50))
        self.assertFalse(z._is_daytime(_states(15), {"state": "above_horizon"}, now=71))

    def test_brief_dip_does_not_count(self):
        z, _ = _zone()
        z._is_daytime(_states(3000), {"state": "above_horizon"}, now=0)
        z._is_daytime(_states(10), {"state": "above_horizon"}, now=10)    # nube / sombra
        z._is_daytime(_states(500), {"state": "above_horizon"}, now=30)   # vuelve la luz
        self.assertTrue(z._is_daytime(_states(10), {"state": "above_horizon"}, now=80))
        self.assertFalse(z._is_daytime(_states(10), {"state": "above_horizon"}, now=141))

    def test_unreadable_sensor_falls_back_to_sun(self):
        z, _ = _zone()
        self.assertFalse(z._is_daytime(_states("unavailable"), {"state": "below_horizon"}))
        self.assertTrue(z._is_daytime(_states("unknown"), {"state": "above_horizon"}))

    def test_without_sensor_uses_sun(self):
        z, _ = _zone(daylight_sensor="")
        self.assertTrue(z._is_daytime(_states(0), {"state": "above_horizon"}))

    def test_closes_by_light_even_with_sun_above_horizon(self):
        z, ws = _zone(daylight_lux_for_seconds=0)
        z.decide_and_act(_states(3000))
        ws.calls.clear()
        z.decide_and_act(_states(12, sun_up=True))
        self.assertEqual(ws.calls, [("set_cover_position", 0, "cover.c")])


if __name__ == "__main__":
    unittest.main()
