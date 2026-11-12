"""Luz que se queda inalcanzable (Matter/Wi-Fi/corte) y vuelve, con el
ZoneRunner real y la configuracion real del Despacho. Caso real 1-nov: el
plafon del despacho y el techo del salon (Matter) pasaron horas
`unavailable`; Lighting lo leia como "apagada a mano" y, al volver, ni la
encendia (solo se enciende en el flanco de la regla) ni la reajustaba."""
import copy, os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import lighting.zone_runner as zr  # noqa: E402

L = "light.plafon_invitados"
PRES = "binary_sensor.presencia"
LUX = "sensor.lux"
CFG = {"auto_off": True, "auto_on": True, "lux_sensor": LUX, "max_brightness_pct": 100,
       "max_color_temp_kelvin": 6500, "min_brightness_pct": 1, "min_color_temp_kelvin": 2700,
       "name": "Despacho", "occupied_states": ["on"], "off_delay_seconds": 0,
       "plant_mode_enabled": False, "plant_mode_lights": [], "presence_boost_enabled": False,
       "presence_entities": [PRES], "reapply_minutes": 1, "respect_manual_changes": True,
       "rules_text": f"Normal; luces={L}", "target_lux": 50, "transition_seconds": 2}


def _sun():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    return {"next_rising": (now + timedelta(hours=10)).isoformat(), "next_setting": (now + timedelta(hours=21)).isoformat(),
            "next_noon": (now + timedelta(hours=15)).isoformat(), "next_midnight": (now + timedelta(hours=4)).isoformat()}


class _HA:
    """HA simulado: la luz aplica lo que se le manda salvo si esta inalcanzable."""
    def __init__(self):
        self.states = {
            PRES: {"entity_id": PRES, "state": "on", "attributes": {}},
            LUX: {"entity_id": LUX, "state": "3", "attributes": {}},
            L: {"entity_id": L, "state": "off", "attributes": {}},
            "sun.sun": {"entity_id": "sun.sun", "state": "below_horizon", "attributes": _sun()},
        }
        self.calls = []

    def get_states(self):
        return list(copy.deepcopy(self.states).values())

    def call_service(self, domain, service, service_data=None, target=None, **kw):
        data = service_data
        ent = (target or {}).get("entity_id") or (data or {}).get("entity_id")
        ents = ent if isinstance(ent, list) else [ent]
        self.calls.append((service, dict(data or {})))
        for e in ents:
            st = self.states.get(e)
            if st is None or st["state"] == "unavailable":
                continue
            if service == "turn_on":
                st["state"] = "on"
                a = st["attributes"]
                if "brightness_pct" in (data or {}):
                    a["brightness"] = round(data["brightness_pct"] * 255 / 100)
                if "color_temp_kelvin" in (data or {}):
                    a["color_temp_kelvin"] = data["color_temp_kelvin"]
            elif service == "turn_off":
                st["state"] = "off"


class UnreachableAndBack(unittest.TestCase):
    def setUp(self):
        self.ha = _HA()
        self.z = zr.ZoneRunner("0fbbad0a", dict(CFG), self.ha)
        self.z._verify_timer = None
        self.z._schedule_fast_verify = lambda: None

    def cycle(self):
        self.z.handle_reactive_event({s["entity_id"]: s for s in self.ha.get_states()})

    def light(self):
        return self.ha.states[L]

    def test_comes_back_off_is_turned_on(self):
        self.cycle()
        self.assertEqual(self.light()["state"], "on")             # entra: se enciende
        self.cycle()                                              # confirmada
        self.ha.states[L] = {"entity_id": L, "state": "unavailable", "attributes": {}}
        for _ in range(5):
            self.cycle()
        self.assertFalse(self.z._state.get("manual_override", {}).get(L))   # no es "apagada a mano"
        self.ha.states[L] = {"entity_id": L, "state": "off", "attributes": {}}   # vuelve apagada
        self.cycle()
        self.assertEqual(self.light()["state"], "on")

    def test_comes_back_on_with_factory_values_is_readapted(self):
        self.cycle(); self.cycle()
        wanted = (self.light()["attributes"].get("brightness"), self.light()["attributes"].get("color_temp_kelvin"))
        self.ha.states[L] = {"entity_id": L, "state": "unavailable", "attributes": {}}
        self.cycle()
        self.ha.states[L] = {"entity_id": L, "state": "on",
                             "attributes": {"brightness": 255, "color_temp_kelvin": 4000}}  # tras el corte
        self.cycle()
        self.assertEqual((self.light()["attributes"]["brightness"], self.light()["attributes"]["color_temp_kelvin"]), wanted)

    def _colour_orders(self):
        return [d for svc, d in self.ha.calls if svc == "turn_on" and "color_temp_kelvin" in d]

    def test_turn_on_by_presence_sends_the_colour_twice_and_then_once(self):
        self.cycle()
        self.assertEqual(len(self._colour_orders()), 2)           # desde apagado: dos veces
        self.ha.calls.clear()
        for _ in range(5):
            self.cycle()
        self.assertEqual(self._colour_orders(), [])               # ya encendida: nada de mas

    def test_manual_command_looks_at_the_light_as_it_is_now(self):
        self.cycle()                                              # la foto del ciclo: apagada
        self.ha.calls.clear()
        self.z.manual_command(on=True, color_temp_kelvin=3000)    # ya encendida: una sola orden
        self.assertEqual(len(self._colour_orders()), 1)
        self.cycle()                                              # la foto del ciclo: encendida
        self.light()["state"] = "off"                             # la apagan sin que la zona se entere
        self.ha.calls.clear()
        self.z.manual_command(on=True, color_temp_kelvin=3000)
        self.assertEqual(len(self._colour_orders()), 2)           # desde apagado: dos veces

    def test_nothing_sent_while_unreachable(self):
        self.cycle(); self.cycle()
        self.ha.states[L] = {"entity_id": L, "state": "unavailable", "attributes": {}}
        self.ha.calls.clear()
        for _ in range(5):
            self.cycle()
        self.assertEqual(self.ha.calls, [])

    def test_light_outside_active_rule_coming_back_on_is_turned_off(self):
        # Salon: con la TV encendida el techo no esta en la regla activa; tras el
        # corte vuelve ENCENDIDO al 68 % -- no es un encendido manual.
        self.z = zr.ZoneRunner("cb850479", dict(CFG, rules_text=(
            f"Normal; si media_player.tv=off; luces={L}\nTV; si media_player.tv=playing; luces=light.lampara")), self.ha)
        self.z._schedule_fast_verify = lambda: None
        self.ha.states["media_player.tv"] = {"entity_id": "media_player.tv", "state": "playing", "attributes": {}}
        self.ha.states["light.lampara"] = {"entity_id": "light.lampara", "state": "off", "attributes": {}}
        self.cycle(); self.cycle()
        self.assertEqual(self.light()["state"], "off")
        self.ha.states[L] = {"entity_id": L, "state": "unavailable", "attributes": {}}
        self.cycle()
        self.ha.states[L] = {"entity_id": L, "state": "on", "attributes": {"brightness": 173}}
        self.cycle()
        self.assertEqual(self.light()["state"], "off")

    def test_restart_after_it_came_back(self):
        # Produccion 1-nov: la luz ya habia vuelto cuando se actualiza el add-on.
        # Estado guardado: orden abandonada + marca de "manual" por el valor de
        # fabrica. Tras reiniciar, el primer ciclo la recupera.
        state = {"active_rule": "Normal", "occupied": True,
                 "commanded": {L: {"brightness_pct": 1, "color_temp_kelvin": 2700, "confirmed": False,
                                   "gave_up": True, "attempts": 3, "first_ts": 1.0, "ts": 1.0}},
                 "manual_override": {L: True}}
        self.ha.states[L] = {"entity_id": L, "state": "on", "attributes": {"brightness": 173}}
        self.z = zr.ZoneRunner("0fbbad0a", dict(CFG), self.ha, state=state)
        self.z._schedule_fast_verify = lambda: None
        self.cycle()
        self.assertEqual(self.light()["attributes"]["brightness"], 3)        # 1 % de la curva
        self.assertFalse(self.z._state.get("manual_override", {}).get(L))

    def test_no_transition_lights_get_transition_zero(self):
        self.z = zr.ZoneRunner("0df2d936", dict(CFG, rules_text=f"Normal; luces={L},light.barra",
                                                no_transition_lights=[L]), self.ha)
        self.z._schedule_fast_verify = lambda: None
        self.ha.states["light.barra"] = {"entity_id": "light.barra", "state": "off", "attributes": {}}
        self.cycle()
        sent = {c[1].get("transition") for c in self.ha.calls if c[0] == "turn_on"}
        self.assertEqual(sent, {0.0, 2.0})

    def test_stale_lights_are_pruned_from_state(self):
        state = {"commanded": {L: {"brightness_pct": 1}, "tplink:7bf176f7": {"brightness_pct": 100}},
                 "manual_override": {"tplink:13766db2": True}}
        z = zr.ZoneRunner("0df2d936", dict(CFG), self.ha, state=state)
        self.assertEqual(set(z._state["commanded"]), {L})
        self.assertEqual(z._state["manual_override"], {})

    def test_non_numeric_lux_sensor_warns_once(self):
        z = zr.ZoneRunner("90a448df", dict(CFG, lux_sensor=L), self.ha)
        z._schedule_fast_verify = lambda: None
        with self.assertLogs("lighting.zone_runner", level="WARNING") as cm:
            self.cycle_with(z); self.cycle_with(z)
        self.assertEqual(sum("no da un numero" in m for m in cm.output), 1)

    def cycle_with(self, z):
        z.handle_reactive_event({s["entity_id"]: s for s in self.ha.get_states()})

    def test_postponed_until_ha_state_arrives(self):
        self.z.handle_reactive_event({})                          # arranque: aun sin estados
        self.assertTrue(self.z.postponed)
        self.cycle()
        self.assertFalse(self.z.postponed)

    def test_manual_off_still_respected(self):
        self.cycle(); self.cycle()
        self.ha.states[L]["state"] = "off"                        # la apaga alguien
        self.cycle(); self.cycle()
        self.assertEqual(self.light()["state"], "off")


if __name__ == "__main__":
    unittest.main()
