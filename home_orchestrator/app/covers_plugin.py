"""
Plugin de Covers (persianas/toldos) para el nucleo Home Orchestrator --
mismo patron de zonas que climate_plugin.py/lighting_plugin.py: una sola
conexion WebSocket compartida para eventos reactivos, zonas persistidas
en `covers/zone_store.py`, un `ZoneRunner` por zona (ver
`covers/zone_runner.py`).

Por que un plugin propio, ni parte de Lighting ni de Climate: una
persiana no es una luz (`light.*`) ni un actuador de calor/frio
(`heat_switches`/`cool_switches`) -- es su propio dominio de HA
(`cover.*`, posicion 0-100%) con una logica propia (proteccion solar por
orientacion de ventana, cierre nocturno por privacidad) que toca tanto
"luz" como "clima" sin ser ninguna de las dos. Controla SOLO entidades
`cover.*` ya existentes en HA (Zigbee/Matter/lo que sea que las exponga)
via los servicios estandar `cover.set_cover_position`/`open_cover`/
`close_cover` -- a diferencia de Lighting, no necesita resolver refs de
otro plugin via `device_registry.py`, ninguna persiana de esta casa la
gestiona otro plugin de este addon.
"""

from __future__ import annotations

import logging
import threading

import flask

import ha_websocket
from covers import zone_store
from covers.zone_runner import ZoneRunner
from plugin_base import Plugin

log = logging.getLogger("covers_plugin")

DEFAULT_REAPPLY_MINUTES = 5


class CoversPlugin(Plugin):
    slug = "covers"
    name = "Covers Orchestrator"
    version = "0.6.0"

    def __init__(self) -> None:
        self._runners: dict[str, ZoneRunner] = {}
        self._zone_stops: dict[str, threading.Event] = {}
        self._zone_reactive: dict[str, ha_websocket.ReactiveTrigger] = {}
        self._ws = ha_websocket.shared()
        self._ws.subscribe("covers", self._on_entity_change)
        self._app = flask.Flask("covers_plugin", template_folder="covers_templates")
        self._register_routes()

    def flask_app(self):
        return self._app

    def _register_routes(self) -> None:
        app = self._app

        @app.get("/")
        def _index():
            return flask.render_template("index.html")

        @app.get("/api/covers")
        def _list_covers():
            """Entidades `cover.*` conocidas por HA ahora mismo -- para el
            selector de la interfaz, mismo patron que Lighting usa para
            `light.*`."""
            try:
                states = self._ws.get_states()
            except Exception:
                log.exception("Fallo listando entidades cover.*")
                return flask.jsonify([])
            out = [
                {
                    "entity_id": s["entity_id"],
                    "name": (s.get("attributes") or {}).get("friendly_name", s["entity_id"]),
                    "state": s.get("state"),
                }
                for s in states
                if s.get("entity_id", "").startswith("cover.")
            ]
            out.sort(key=lambda x: x["name"].lower())
            return flask.jsonify(out)

        @app.get("/api/zones")
        def _list_zones():
            zones = zone_store.load_zones()
            out = []
            for z in zones:
                runner = self._runners.get(z["id"])
                item = {"id": z["id"], "config": z["config"]}
                if runner:
                    item["live"] = {
                        "reason": runner.reason,
                        "desired_position": runner.desired_position,
                        "sun_position_ok": runner.sun_position_ok,
                        "learned_sun_protection_position_pct": z.get("state", {}).get("learned_sun_protection_position_pct"),
                        "learned_window_azimuth_min": z.get("state", {}).get("learned_window_azimuth_min"),
                        "learned_window_azimuth_max": z.get("state", {}).get("learned_window_azimuth_max"),
                    }
                out.append(item)
            return flask.jsonify(out)

        @app.post("/api/zones")
        def _add_zone():
            payload = flask.request.get_json(force=True) or {}
            zone = zone_store.add_zone(payload)
            self._start_zone(zone)
            return flask.jsonify(zone), 201

        @app.put("/api/zones/<zone_id>")
        def _update_zone(zone_id):
            payload = flask.request.get_json(force=True) or {}
            zone = zone_store.update_zone_config(zone_id, payload)
            if not zone:
                return flask.jsonify({"error": "zona no encontrada"}), 404
            self._stop_zone(zone_id)
            self._start_zone(zone)
            return flask.jsonify(zone)

        @app.delete("/api/zones/<zone_id>")
        def _delete_zone(zone_id):
            self._stop_zone(zone_id)
            ok = zone_store.delete_zone(zone_id)
            return flask.jsonify({"deleted": ok})

        @app.post("/api/zones/<zone_id>/refresh")
        def _refresh_zone(zone_id):
            """Fuerza una decision ahora mismo -- util para probar una
            zona recien creada sin esperar al ciclo periodico."""
            runner = self._runners.get(zone_id)
            if not runner:
                return flask.jsonify({"error": "zona no encontrada o no arrancada"}), 404
            try:
                runner.decide_and_act()
                zone_store.update_zone_state(zone_id, runner.to_persisted_state())
            except Exception:
                log.exception("Fallo forzando decision de zona %s", zone_id)
                return flask.jsonify({"error": "fallo forzando la decision de la zona"}), 500
            return flask.jsonify({"ok": True, "reason": runner.reason, "desired_position": runner.desired_position})

        @app.get("/api/status")
        def _status():
            return flask.jsonify({
                "version": self.version, "zones": len(self._runners),
                "ws_connected": getattr(self._ws, "connected", False),
            })

    # ------------------------------------------------------------ arranque -

    def start_background_threads(self) -> None:
        for zone in zone_store.load_zones():
            self._start_zone(zone)

    def _start_zone(self, zone: dict) -> None:
        zone_id = zone["id"]
        cfg = zone["config"]
        runner = ZoneRunner(zone_id, cfg, ws=self._ws, state=zone.get("state"))
        self._runners[zone_id] = runner

        if not self._ws.connected:
            log.info(
                "Zona covers %s: decision inicial pospuesta -- WebSocket de HA aun sin "
                "conectar, se resolvera en el primer evento reactivo o ciclo periodico", zone_id,
            )
        else:
            try:
                runner.decide_and_act()
                zone_store.update_zone_state(zone_id, runner.to_persisted_state())
            except Exception:
                log.warning(
                    "Zona covers %s: fallo en la decision inicial -- se reintenta en el "
                    "proximo ciclo", zone_id, exc_info=True,
                )

        reapply_minutes = int(cfg.get("reapply_minutes", DEFAULT_REAPPLY_MINUTES) or DEFAULT_REAPPLY_MINUTES)
        stop = threading.Event()
        self._zone_stops[zone_id] = stop
        threading.Thread(
            target=self._periodic_loop,
            args=(zone_id, reapply_minutes, runner, stop),
            name=f"covers-periodic-{zone_id}",
            daemon=True,
        ).start()

        disparador = ha_websocket.ReactiveTrigger(
            lambda zid=zone_id: self._run_zone_reactive(zid), min_interval_seconds=0.2,
        )
        self._zone_reactive[zone_id] = disparador
        threading.Thread(
            target=disparador.worker_loop, name=f"covers-reactiva-{zone_id}", daemon=True,
        ).start()
        disparador.trigger()

        self._refresh_watched_entities()

    def _stop_zone(self, zone_id: str) -> None:
        self._runners.pop(zone_id, None)
        stop = self._zone_stops.pop(zone_id, None)
        if stop is not None:
            stop.set()
        self._zone_reactive.pop(zone_id, None)
        self._refresh_watched_entities()

    def _refresh_watched_entities(self) -> None:
        watched: set[str] = set()
        for runner in self._runners.values():
            try:
                watched |= runner.watched_entities()
            except Exception:
                log.exception("Fallo obteniendo watched_entities de zona %s", runner.zone_id)
        self._ws.set_watched_entities(watched, key="covers")

    # ----------------------------------------------------------- reactivo -

    def _on_entity_change(self, entity_id: str, new_state: dict) -> None:
        afectadas = []
        for zone_id, runner in list(self._runners.items()):
            try:
                if entity_id in runner.watched_entities():
                    afectadas.append(zone_id)
            except Exception:
                log.exception("Fallo mirando si la zona %s vigila %s", zone_id, entity_id)
        for zone_id in afectadas:
            disparador = self._zone_reactive.get(zone_id)
            if disparador is not None:
                disparador.trigger()

    def _run_zone_reactive(self, zone_id: str) -> None:
        runner = self._runners.get(zone_id)
        if runner is None:
            return
        try:
            states = {s.get("entity_id"): s for s in self._ws.get_states() if s.get("entity_id")}
        except Exception:
            log.exception("Fallo leyendo estados de HA para la zona %s", zone_id)
            states = None
        try:
            runner.handle_reactive_event(states)
            zone_store.update_zone_state(zone_id, runner.to_persisted_state())
        except Exception:
            log.exception("Fallo en ciclo reactivo de zona covers %s", zone_id)

    # ------------------------------------------------------------ periodo -

    def _periodic_loop(self, zone_id: str, reapply_minutes: int, runner: ZoneRunner, stop: threading.Event) -> None:
        interval = max(reapply_minutes, 1) * 60
        while not stop.wait(interval):
            if self._runners.get(zone_id) is not runner:
                return
            try:
                runner.handle_periodic_reapply()
                zone_store.update_zone_state(zone_id, runner.to_persisted_state())
            except Exception:
                log.exception("Fallo en reaplicacion periodica de zona covers %s", zone_id)
