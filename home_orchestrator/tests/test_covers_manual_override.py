"""Un cambio a mano en una persiana se respeta hasta la siguiente transicion de
la zona, no para siempre (2-nov: las tres persianas marcadas para siempre y la
zona sin mover ninguna)."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import covers.zone_runner as zr  # noqa: E402


class _WS:
    def __init__(self): self.calls = []
    def call_service(self, domain, service, service_data=None, target=None):
        self.calls.append((service, (service_data or {}).get("position"), target["entity_id"]))


def _states(sun_up, pos):
    return {"sun.sun": {"state": "above_horizon" if sun_up else "below_horizon", "attributes": {"elevation": 30 if sun_up else -10, "azimuth": 180}},
            "cover.c": {"state": "open", "attributes": {"current_position": pos, "supported_features": 15}}}


class ManualOverride(unittest.TestCase):
    def _zone(self):
        ws = _WS()
        z = zr.ZoneRunner("z", {"cover_entities": ["cover.c"], "night_close_enabled": True, "day_open_enabled": True}, ws)
        return z, ws

    def test_manual_change_respected_until_next_transition(self):
        z, ws = self._zone()
        z.decide_and_act(_states(False, 100))                 # anochece -> cierra
        self.assertEqual(ws.calls, [("set_cover_position", 0, "cover.c")])
        ws.calls.clear()
        z.decide_and_act(_states(False, 60))                  # alguien la abre a mano
        self.assertEqual(ws.calls, [])                        # se respeta
        self.assertTrue(z._state["manual_override"]["cover.c"])
        z.decide_and_act(_states(True, 60))                   # amanece: transicion
        self.assertEqual(ws.calls, [("set_cover_position", 100, "cover.c")])
        self.assertEqual(z._state["manual_override"], {})

    def test_stale_persisted_flag_does_not_block_forever(self):
        ws = _WS()
        z = zr.ZoneRunner("z", {"cover_entities": ["cover.c"], "night_close_enabled": True, "day_open_enabled": True}, ws,
                          state={"manual_override": {"cover.c": True}, "commanded": {"cover.c": 100}, "last_desired": 0})
        z.decide_and_act(_states(True, 0))
        self.assertEqual(ws.calls, [("set_cover_position", 100, "cover.c")])


    def test_moving_or_already_in_place_is_not_manual(self):
        # 2-nov 18:13: la del Despacho se marcaba al leerla "closing" a 100 %, y la
        # puerta del patio (ya cerrada) se comparaba con la apertura de la manana.
        ws = _WS()
        z = zr.ZoneRunner("z", {"cover_entities": ["cover.c", "cover.d"], "night_close_enabled": True, "day_open_enabled": True}, ws,
                          state={"commanded": {"cover.c": 100, "cover.d": 100}, "last_desired": 100})
        st = _states(False, 100)
        st["cover.d"] = {"state": "closed", "attributes": {"current_position": 0, "supported_features": 15}}
        z.decide_and_act(st)
        self.assertEqual(ws.calls, [("set_cover_position", 0, "cover.c")])
        st["cover.c"] = {"state": "closing", "attributes": {"current_position": 100, "supported_features": 15}}
        z.decide_and_act(st)
        self.assertEqual(z._state["manual_override"], {})


if __name__ == "__main__":
    unittest.main()
