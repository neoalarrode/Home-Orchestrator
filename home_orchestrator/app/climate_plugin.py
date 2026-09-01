"""
Plugin de Climate para el nucleo Home Orchestrator -- segundo plugin real,
tras Battery. Expone termostatos NATIVOS de HA (`climate.*`, con
HomeKit/Matter incluido) via MQTT Discovery (ver climate/mqtt_climate.py),
con toda la logica de decision portada de Climate Orchestrator
(climate/zone_runner.py) y conectada por WebSocket (nunca REST, ver
ha_websocket.py) tanto para eventos reactivos como para consultas
puntuales (historial, servicios...).

Una sola conexion WebSocket y una sola conexion MQTT para TODAS las zonas
de este plugin -- no una por zona.
"""

from __future__ import annotations

import logging
import threading
import time

import flask

import device_registry
import ha_mqtt
import ha_websocket
from climate import zone_store
from climate.mqtt_climate import MqttClimateZone
from climate.zone_runner import ZoneRunner, _resolve_min_max_temp, zone_stagger_seconds
from plugin_base import Plugin

log = logging.getLogger("climate_plugin")

DEFAULT_FORECAST_REFRESH_MINUTES = 10
REACTIVE_MIN_INTERVAL_SECONDS = 5


class ClimatePlugin(Plugin):
    slug = "climate"
    name = "Climate Orchestrator"
    version = "0.7.5"

    def __init__(self) -> None:
        self._runners: dict[str, ZoneRunner] = {}
        self._mqtt_zones: dict[str, MqttClimateZone] = {}
        # Conexion COMPARTIDA del core -- ver ha_websocket.shared().
        self._ws = ha_websocket.shared()
        self._ws.subscribe("climate", self._on_entity_change)
        self._mqtt = ha_mqtt.HAMqttClient(client_id="home_orchestrator_climate")
        self._reactive = ha_websocket.ReactiveTrigger(self._run_reactive_cycle)
        self._app = flask.Flask("climate_plugin", template_folder="climate_templates")
        # Los actuadores de otros plugins (Tuya/Shelly/... hoy) se
        # resuelven via el registro COMPARTIDO device_registry.py,
        # filtrando siempre por capacidad "climate" -- ver
        # is_bridge_ref()/resolve_bridge_handle() mas abajo. Sin ningun
        # proveedor de esa capacidad cargado, las zonas que referencien
        # un actuador de otro plugin simplemente no lo controlan (ver
        # ZoneRunner._resolve_bridge_handle), no revientan.
        # BUG REAL, confirmado por fuzzing adversarial: dos zonas que
        # comparten el mismo switch fisico (p.ej. una caldera con dos
        # circuitos declarados como dos zonas) deciden y actuan de forma
        # totalmente independiente, sin ninguna coordinacion. Una zona en
        # emergencia de seguridad (por debajo de min_temp) puede encender
        # el switch, y en el MISMO ciclo reactivo otra zona -- que no sabe
        # que lo comparte -- lo apaga por su propia logica, dejando a la
        # primera sin calefaccion pese a estar en su caso de mayor
        # gravedad. `_actuator_cycle_claims` registra, solo durante la
        # ejecucion de un ciclo reactivo, quien ha tocado cada actuador y
        # con que urgencia -- ver arbitrate_switch_command().
        self._actuator_cycle_claims: dict[str, tuple[str, bool, bool]] = {}
        self._register_routes()

    def arbitrate_switch_command(self, entity_id: str, zone_id: str, desired_on: bool, urgent: bool) -> bool:
        """Decide si una zona puede aplicar su comando sobre un switch que
        puede estar compartido con otra zona, dentro del mismo ciclo
        reactivo. Regla minima y deliberadamente conservadora: si OTRA
        zona ya encendio este mismo actuador en este ciclo en modo de
        emergencia (`urgent`), ninguna zona puede apagarlo hasta el
        siguiente ciclo -- evita que una decision no urgente deshaga, en
        el mismo instante, la decision de seguridad de otra zona. Fuera de
        ese caso concreto, se conserva el comportamiento de siempre
        (ultima decision del ciclo manda), que es correcto cuando el
        actuador de verdad pertenece a una sola zona (el caso normal)."""
        prior = self._actuator_cycle_claims.get(entity_id)
        if prior is not None:
            prior_zone, prior_on, prior_urgent = prior
            if prior_zone != zone_id and prior_urgent and prior_on and not desired_on:
                log.warning(
                    "Conflicto de actuador compartido en %s: la zona %s intento apagarlo en el "
                    "mismo ciclo reactivo en que la zona %s lo encendio en modo de emergencia -- "
                    "se ignora la orden de apagado hasta el siguiente ciclo.",
                    entity_id, zone_id, prior_zone,
                )
                return False
        self._actuator_cycle_claims[entity_id] = (zone_id, desired_on, urgent)
        return True

    def is_bridge_ref(self, ref: str) -> bool:
        return device_registry.is_bridge_ref(ref)

    def resolve_bridge_handle(self, ref: str):
        """`ref` = '<prefijo>:<device_id>[:<indice>]' -> handle, o None
        si el prefijo no tiene proveedor registrado ahora mismo, o si no
        ofrece la capacidad "climate" para ese ref."""
        return device_registry.resolve(ref, "climate")

    def get_history(self, ref: str, days: int) -> list[dict]:
        """Historico de un actuador de otro plugin, para que
        thermal_model.py aprenda su inercia termica igual que ya aprende
        de un `climate.*`/switch de HA -- `[]` si el proveedor no expone
        historico (capacidad opcional, ver TuyaPlugin.get_actuator_history)
        o si el prefijo no tiene proveedor registrado ahora mismo."""
        prefix, rest = ref.split(":", 1)
        provider = device_registry.get_provider(prefix)
        getter = getattr(provider, "get_actuator_history", None)
        if getter is None:
            return []
        parts = rest.split(":", 1)
        device_id = parts[0]
        index = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        try:
            return getter(device_id, index, days)
        except Exception:
            log.exception("Fallo obteniendo historico de '%s'", ref)
            return []

    # --------------------------------------------------------------- Flask -

    def flask_app(self):
        return self._app

    def _register_routes(self) -> None:
        app = self._app

        @app.get("/")
        def _index():
            return flask.render_template("index.html")

        @app.get("/api/actuators")
        def _list_actuators():
            """Actuadores climate.* que OTROS plugins ofrecen (Tuya hoy,
            otra marca mañana) -- agregado de todos los proveedores
            registrados, filtrando los que ya esten en uso en CUALQUIER
            zona (no interesa mostrar dos veces algo que ya esta añadido,
            ni permitir asignarlo por error a una segunda zona a la vez)."""
            used_refs = {
                ref
                for z in zone_store.load_zones()
                for ref in (z["config"].get("climate_entities") or [])
                if ":" in ref
            }
            out = []
            for actuator in device_registry.list_actuators("climate"):
                actuator = dict(actuator)
                actuator["already_used"] = actuator.get("ref") in used_refs
                out.append(actuator)
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
                        "available": runner.available,
                        "hvac_mode": runner.hvac_mode,
                        "hvac_action": runner.hvac_action,
                        "current_temperature": runner.current_temperature,
                        "temp_history": list(runner.temp_history),
                        "target_temperature": runner.target_temperature,
                        "target_temperature_low": runner.target_temperature_low,
                        "target_temperature_high": runner.target_temperature_high,
                        "reason": runner.reason,
                        # Para que la tarjeta de termostato del dashboard
                        # sepa que opciones ofrecer -- no todas las zonas
                        # soportan los mismos modos (p.ej. una sin
                        # actuador de frio no tiene "cool"/"heat_cool").
                        "hvac_modes": runner.hvac_modes,
                        "preset_mode": runner._preset_mode,
                        "preset_modes": runner._preset_modes,
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

        @app.get("/api/zones/<zone_id>/forecast")
        def _zone_forecast(zone_id):
            """Datos del grafico de 24h de la zona (historico real + mitad
            futura proyectada EN VIVO con el mismo motor de decision, ver
            climate/zone_forecast.py) -- se calcula bajo demanda, nunca en
            el ciclo periodico normal (implica varias consultas de
            historico a HA, deliberadamente fuera del camino caliente)."""
            runner = self._runners.get(zone_id)
            if not runner:
                return flask.jsonify({"error": "zona no encontrada o no arrancada"}), 404
            try:
                points = runner.build_forecast_chart()
            except Exception:
                log.exception("Fallo calculando el grafico de zona %s", zone_id)
                return flask.jsonify({"error": "fallo calculando la previsión"}), 500
            return flask.jsonify(points)

        @app.post("/api/zones/<zone_id>/refresh")
        def _refresh_zone(zone_id):
            """Fuerza una decisión ahora mismo, sin esperar al próximo evento
            reactivo o al refresco periódico -- util para diagnostico y para
            verificar una zona recien creada."""
            runner = self._runners.get(zone_id)
            if not runner:
                return flask.jsonify({"error": "zona no encontrada o no arrancada"}), 404
            try:
                runner.decide_and_act()
                zone_store.update_zone_state(zone_id, runner.to_persisted_state())
            except Exception:
                log.exception("Fallo forzando decision de zona %s", zone_id)
                return flask.jsonify({"error": "fallo forzando la decision de la zona"}), 500
            return flask.jsonify({"ok": True})

        # ---- comandos de la tarjeta de termostato del Dashboard --------
        #
        # `ZoneRunner.set_temperature`/`set_hvac_mode`/`set_preset_mode` ya
        # existian -- son el MISMO mecanismo que ya usa la orden MQTT real
        # del climate.* expuesto a HA (ver mqtt_climate.py), asi que la
        # tarjeta del dashboard no depende de que HA tenga bien resuelta
        # la entidad ni de ir a buscar su entity_id: habla DIRECTO con el
        # runner, exactamente igual que si la orden viniera de HomeKit.
        # Los tres terminan en `decide_and_act()` (que ya publica el
        # estado nuevo por MQTT solo, ver `_maybe_publish_state`), asi que
        # aqui solo hace falta persistir el estado tras la llamada -- mismo
        # patron que `/refresh` de arriba.
        def _zone_command(zone_id, fn):
            runner = self._runners.get(zone_id)
            if not runner:
                return flask.jsonify({"error": "zona no encontrada o no arrancada"}), 404
            try:
                fn(runner)
                zone_store.update_zone_state(zone_id, runner.to_persisted_state())
            except Exception:
                log.exception("Fallo aplicando comando en zona %s", zone_id)
                return flask.jsonify({"error": "fallo aplicando el comando en la zona"}), 500
            return flask.jsonify({"ok": True})

        @app.post("/api/zones/<zone_id>/set_temperature")
        def _set_temperature(zone_id):
            payload = flask.request.get_json(force=True) or {}
            return _zone_command(zone_id, lambda r: r.set_temperature(
                single=payload.get("temperature"), low=payload.get("target_temp_low"),
                high=payload.get("target_temp_high"),
            ))

        @app.post("/api/zones/<zone_id>/set_hvac_mode")
        def _set_hvac_mode(zone_id):
            payload = flask.request.get_json(force=True) or {}
            hvac_mode = payload.get("hvac_mode")
            if not hvac_mode:
                return flask.jsonify({"error": "falta 'hvac_mode'"}), 400
            return _zone_command(zone_id, lambda r: r.set_hvac_mode(hvac_mode))

        @app.post("/api/zones/<zone_id>/set_preset_mode")
        def _set_preset_mode(zone_id):
            payload = flask.request.get_json(force=True) or {}
            preset_mode = payload.get("preset_mode")
            if not preset_mode:
                return flask.jsonify({"error": "falta 'preset_mode'"}), 400
            return _zone_command(zone_id, lambda r: r.set_preset_mode(preset_mode))

        @app.get("/api/status")
        def _status():
            return flask.jsonify(
                {
                    "version": self.version,
                    "zones": len(self._runners),
                    "ws_connected": getattr(self._ws, "connected", False),
                    "mqtt_connected": self._mqtt.connected,
                }
            )

    # ------------------------------------------------------------- arranque

    def start_background_threads(self) -> None:
        threading.Thread(target=self._reactive.worker_loop, name="climate-reactive", daemon=True).start()
        self._mqtt.connect()

        zones = zone_store.load_zones()
        started = 0
        for zone in zones:
            # BUG REAL, confirmado por fuzzing adversarial: sin este
            # try/except, una zona con config corrupta (min_temp no
            # numerico, actuador mal declarado, cualquier fallo dentro de
            # `ZoneRunner.__init__`) tumbaba `_start_zone` ANTES de
            # terminar el bucle -- todas las zonas restantes en la lista
            # se quedaban sin arrancar en absoluto, sin ningun aviso mas
            # alla de la traza de Python en el log. Una zona rota ahora se
            # salta y se avisa; el resto arrancan con normalidad.
            try:
                self._start_zone(zone)
                started += 1
            except Exception:
                log.exception(
                    "Fallo arrancando la zona %s -- se omite, el resto de zonas siguen arrancando",
                    zone.get("id", "?"),
                )
        self._refresh_watched_entities()

        if started < len(zones):
            log.info("Plugin Climate arrancado con %d/%d zona(s) (el resto fallo al arrancar, ver arriba)", started, len(zones))
        else:
            log.info("Plugin Climate arrancado con %d zona(s)", started)

    def _persist_zone_state(self, runner) -> None:
        """Guarda el estado de la zona tras aplicar un comando. El estado hacia
        HA ya lo publica `ZoneRunner._maybe_publish_state` dentro de
        `decide_and_act`, asi que aqui solo hace falta persistir -- mismo patron
        que `_zone_command` (HTTP)."""
        zone_store.update_zone_state(runner.zone_id, runner.to_persisted_state())

    def _start_zone(self, zone: dict) -> None:
        zone_id = zone["id"]
        cfg = zone["config"]
        state = zone.get("state") or None
        all_zone_configs = [z["config"] for z in zone_store.load_zones()]

        mqtt_zone = MqttClimateZone(self._mqtt, zone_id, cfg, enabled=cfg.get("expose_to_ha", True))
        runner = ZoneRunner(zone_id, cfg, self._ws, mqtt_zone, all_zone_configs, state=state, bridges=self)
        # Un comando llegado por MQTT persiste el estado igual que el endpoint
        # HTTP equivalente (`_zone_command`) -- antes solo lo hacia el HTTP, asi
        # que una consigna puesta desde HA se perdia al reiniciar el add-on.
        mqtt_zone.bind(runner, after_command=self._persist_zone_state)
        min_temp, max_temp = _resolve_min_max_temp(cfg.get("min_temp", 15.0), cfg.get("max_temp", 30.0), 15.0, 30.0)
        mqtt_zone.publish_discovery(min_temp=min_temp, max_temp=max_temp)

        self._runners[zone_id] = runner
        self._mqtt_zones[zone_id] = mqtt_zone

        refresh_minutes = int(cfg.get("forecast_refresh_minutes", DEFAULT_FORECAST_REFRESH_MINUTES))
        stagger = zone_stagger_seconds(zone_id, refresh_minutes)
        threading.Thread(
            target=self._periodic_loop,
            args=(zone_id, refresh_minutes, stagger),
            name=f"climate-periodic-{zone_id}",
            daemon=True,
        ).start()

        self._refresh_watched_entities()

    def _stop_zone(self, zone_id: str) -> None:
        mqtt_zone = self._mqtt_zones.pop(zone_id, None)
        if mqtt_zone:
            mqtt_zone.remove_discovery()
        self._runners.pop(zone_id, None)
        self._refresh_watched_entities()
        # los hilos periodicos de esta zona se auto-terminan al no
        # encontrarse ya en self._runners (ver _periodic_loop)

    def _refresh_watched_entities(self) -> None:
        watched: set[str] = set()
        for runner in self._runners.values():
            try:
                watched |= runner.watched_entities()
            except Exception:
                log.exception("Fallo obteniendo watched_entities de zona %s", runner.zone_id)
        self._ws.set_watched_entities(watched, key="climate")

    # ----------------------------------------------------------- reactivo -

    def _on_entity_change(self, entity_id: str, new_state: dict) -> None:
        self._reactive.trigger()

    def _run_reactive_cycle(self) -> None:
        self._actuator_cycle_claims = {}
        for zone_id, runner in list(self._runners.items()):
            try:
                runner.handle_reactive_event()
                zone_store.update_zone_state(zone_id, runner.to_persisted_state())
            except Exception:
                log.exception("Fallo en ciclo reactivo de zona %s", zone_id)

    # ------------------------------------------------------------ periodo -

    def _periodic_loop(self, zone_id: str, refresh_minutes: int, stagger: float) -> None:
        time.sleep(stagger)
        while zone_id in self._runners:
            runner = self._runners.get(zone_id)
            if runner is None:
                return
            try:
                runner.handle_forecast_refresh()
                zone_store.update_zone_state(zone_id, runner.to_persisted_state())
            except Exception:
                log.exception("Fallo en refresco periodico de zona %s", zone_id)
            time.sleep(max(refresh_minutes, 1) * 60)
