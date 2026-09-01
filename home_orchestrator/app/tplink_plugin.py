"""
Plugin de TP-Link para el nucleo Home Orchestrator -- puro puente de
ingesta, mismo papel que TuyaPlugin pero usando `python-kasa` (la MISMA
libreria que el componente `tplink` real de Home Assistant) en vez de
una reimplementacion propia del protocolo: no hace falta un perfil
declarativo por dispositivo, `python-kasa` ya dice en tiempo real que
modulos tiene cada uno (`device.modules`).

A diferencia de Tuya (deteccion pasiva por broadcast continuo, algunos
dispositivos solo emiten de cuando en cuando), el descubrimiento de
`python-kasa` es un escaneo ACTIVO (`Discover.discover()` manda un
broadcast y recoge respuestas durante unos segundos) -- no hay nada que un
dispositivo anuncie por su cuenta, asi que no existe un listener de fondo
persistente como `PersistentDiscovery` de Tuya. En su lugar, `_rediscover_loop`
repite ESE MISMO escaneo cada `REDISCOVER_INTERVAL_SECONDS` sin que el
usuario tenga que pulsar nada -- alimenta `/api/discovered` (lo visto hasta
ahora, lectura pasiva) ademas del `/api/discover` bajo demanda, y de paso
reconecta solo cualquier dispositivo ya dado de alta cuya IP haya cambiado
por DHCP (se reconoce por MAC, ver `tplink/device_manager.py`).

Dos formas de usar un dispositivo dado de alta, no excluyentes (mismo
criterio que Tuya):
  - Consumo INTERNO: otro plugin (hoy Lighting) puede pedir un
    `TplinkLightHandle` (ver light_handle()) y controlar la bombilla EN
    EL MISMO PROCESO, sin pasar por Home Assistant.
  - Exposicion opcional a HA por MQTT Discovery (`expose_mqtt` por
    dispositivo, ver tplink/mqtt_tplink.py).
"""

from __future__ import annotations

import logging
import threading

import flask
from kasa import Credentials, Discover

import ha_mqtt
import tplink_store
from plugin_base import Plugin
from tplink.device_manager import TplinkDeviceManager
from tplink.mqtt_tplink import MqttTplinkDevice

log = logging.getLogger("tplink_plugin")

# Espera inicial antes del primer escaneo periodico -- da tiempo a que los
# dispositivos ya dados de alta terminen de conectar en el arranque, para no
# lanzar un escaneo (y un posible reconectar) contra algo que solo estaba
# tardando un poco. Mismo criterio que IDENTIFY_FIRST_DELAY_SECONDS de Tuya.
REDISCOVER_FIRST_DELAY_SECONDS = 60
REDISCOVER_INTERVAL_SECONDS = 5 * 60


class TplinkPlugin(Plugin):
    slug = "tplink"
    name = "TP-Link Orchestrator"
    version = "0.2.3"

    def __init__(self) -> None:
        self._manager = TplinkDeviceManager(
            on_any_change=self._on_device_change,
            on_address_change=self._persist_address,
        )
        self._mqtt = ha_mqtt.HAMqttClient(client_id="home_orchestrator_tplink")
        self._mqtt_devices: dict[str, MqttTplinkDevice] = {}
        self._app = flask.Flask("tplink_plugin", template_folder="tplink_templates")
        self._stop_rediscover = threading.Event()
        self._register_routes()

    def _credentials(self) -> Credentials | None:
        account = tplink_store.load_account()
        if not account["username"] or not account["password"]:
            return None
        return Credentials(account["username"], account["password"])

    # --------------------------------------------------------------- Flask -

    def flask_app(self):
        return self._app

    def _register_routes(self) -> None:
        app = self._app

        @app.get("/")
        def _index():
            return flask.render_template("index.html")

        @app.get("/api/devices")
        def _list_devices():
            devices = tplink_store.load_devices()
            out = []
            for d in devices:
                host = d["config"]["host"]
                item = {"id": d["id"], "config": d["config"]}
                device = self._manager.get_device(d["id"])
                if device is not None:
                    item["live"] = {
                        "connected": self._manager.connected(d["id"]),
                        "is_on": device.is_on,
                        "model": device.model,
                        "device_type": str(device.device_type),
                    }
                out.append(item)
            return flask.jsonify(out)

        @app.post("/api/devices")
        def _add_device():
            payload = flask.request.get_json(force=True) or {}
            device = tplink_store.add_device(payload)
            self._start_device(device)
            return flask.jsonify(device), 201

        @app.put("/api/devices/<device_id>")
        def _update_device(device_id):
            payload = flask.request.get_json(force=True) or {}
            device = tplink_store.update_device(device_id, payload)
            if not device:
                return flask.jsonify({"error": "dispositivo no encontrado"}), 404
            self._stop_device(device_id)
            self._start_device(device)
            return flask.jsonify(device)

        @app.delete("/api/devices/<device_id>")
        def _delete_device(device_id):
            self._stop_device(device_id)
            ok = tplink_store.delete_device(device_id)
            return flask.jsonify({"deleted": ok})

        @app.get("/api/status")
        def _status():
            return flask.jsonify({
                "version": self.version,
                "devices": len(tplink_store.load_devices()),
                "mqtt_connected": self._mqtt.connected,
            })

        @app.get("/api/account")
        def _get_account():
            account = tplink_store.load_account()
            return flask.jsonify({"username": account["username"], "linked": bool(account["username"] and account["password"])})

        @app.post("/api/account")
        def _save_account():
            payload = flask.request.get_json(force=True) or {}
            tplink_store.save_account({"username": payload.get("username", ""), "password": payload.get("password", "")})
            return flask.jsonify({"linked": True})

        # ------------------------------------------------ descubrimiento -
        # Escaneo ACTIVO (ver docstring del modulo) -- nunca añade nada por
        # su cuenta, solo enseña lo que ha respondido. `/api/discover` lo
        # lanza al momento (boton "Buscar ahora"); `/api/discovered` lee sin
        # bloquear lo que el escaneo periodico de fondo ya tiene acumulado --
        # mismo par de rutas y mismo criterio que Tuya (`/api/scan` +
        # `/api/discovered`), para que la interfaz de ambos plugins se
        # comporte igual.

        def _describe_found(found: dict[str, dict]) -> list[dict]:
            added_hosts = {d["config"]["host"] for d in tplink_store.load_devices()}
            return [
                {
                    "host": host,
                    "alias": info.get("alias"),
                    "model": info.get("model"),
                    "device_type": info.get("device_type"),
                    "already_added": host in added_hosts,
                    "needs_auth": info.get("needs_auth", False),
                }
                for host, info in found.items()
            ]

        @app.post("/api/discover")
        def _discover():
            try:
                found = self._manager.discover(self._credentials())
            except Exception:
                log.exception("Fallo escaneando la LAN en busca de dispositivos TP-Link")
                return flask.jsonify({"error": "fallo escaneando la LAN"}), 502
            return flask.jsonify(_describe_found(found))

        @app.get("/api/discovered")
        def _list_discovered():
            return flask.jsonify(_describe_found(self._manager.get_discovered_devices()))

    # --------------------------------------------- localizar lo que se movio

    def _persist_address(self, device_id: str, new_host: str) -> None:
        """Guarda la IP nueva de un dispositivo que se ha movido (DHCP).
        Sin esto el cambio se perderia en el siguiente reinicio: arrancaria
        otra vez contra la IP vieja. Mismo criterio que
        `tuya_plugin.py::_persist_address`."""
        updated = tplink_store.update_device(device_id, {"host": new_host})
        if updated:
            log.info("TP-Link %s: IP actualizada a %s en el almacen", device_id, new_host)
        else:
            log.warning("TP-Link %s: no se encontro en el almacen para guardar la IP nueva", device_id)

    def _rediscover_loop(self) -> None:
        """Escanea la LAN periodicamente sin que el usuario tenga que pulsar
        nada -- alimenta `/api/discovered` y reconecta solo lo que haya
        cambiado de IP (ver `TplinkDeviceManager.rediscover_now`). Mismo
        patron de hilo cancelable que `tuya_plugin.py::_identify_loop`."""
        while not self._stop_rediscover.wait(REDISCOVER_FIRST_DELAY_SECONDS):
            try:
                self._manager.rediscover_now(self._credentials())
            except Exception:
                log.exception("Fallo en el escaneo periodico de TP-Link")
            if self._stop_rediscover.wait(REDISCOVER_INTERVAL_SECONDS - REDISCOVER_FIRST_DELAY_SECONDS):
                return

    # ------------------------------------------------------------- arranque

    def start_background_threads(self) -> None:
        self._manager.start()
        self._mqtt.connect()
        devices = tplink_store.load_devices()
        for device in devices:
            self._start_device(device)
        threading.Thread(
            target=self._rediscover_loop, name="tplink-rediscover", daemon=True,
        ).start()
        log.info("Plugin TP-Link arrancado con %d dispositivo(s)", len(devices))

    def _start_device(self, device: dict) -> None:
        cfg = device["config"]
        if not cfg.get("host"):
            log.warning("Dispositivo TP-Link '%s' sin host -- no se conecta", cfg.get("name") or device["id"])
            return
        try:
            self._manager.add_device(device["id"], cfg["host"], self._credentials())
        except Exception:
            log.exception("Fallo conectando al dispositivo TP-Link '%s'", cfg.get("name") or cfg["host"])
            # Mismo criterio que en tuya_plugin: un fallo de CONEXION no es un
            # fallo de ALTA. Si el dispositivo quedo registrado en el manager
            # (`_discover_and_connect` ya lo registra aunque la primera lectura
            # falle), el sondeo lo va a recuperar, asi que se expone en HA
            # igualmente -- empieza como no disponible. Antes este `return` se
            # saltaba el bloque de MQTT y la entidad no aparecia hasta reiniciar
            # el add-on, aunque el sondeo hubiera levantado el dispositivo.
            if self._manager.get_device(device["id"]) is None:
                return
            log.info(
                "Dispositivo TP-Link '%s' registrado pero sin lectura buena todavia -- se expone "
                "en HA igualmente y el sondeo lo reintenta", cfg.get("name") or cfg["host"],
            )

        if cfg.get("expose_mqtt"):
            mqtt_dev = MqttTplinkDevice(self._mqtt, self._manager, device["id"], cfg.get("name") or cfg["host"])
            mqtt_dev.publish_discovery()
            # Mismo bug de Tuya que ya se corrigio ahi -- se evita desde
            # el principio: sin este publish_state() inicial, la entidad
            # recien expuesta se quedaria en "unknown" hasta el primer
            # sondeo periodico (hasta POLL_INTERVAL_SECONDS de retraso).
            mqtt_dev.publish_state()
            self._mqtt_devices[device["id"]] = mqtt_dev

    def _stop_device(self, device_id: str) -> None:
        mqtt_dev = self._mqtt_devices.pop(device_id, None)
        if mqtt_dev:
            mqtt_dev.remove_discovery()
        self._manager.remove_device(device_id)

    def _on_device_change(self, device_id: str) -> None:
        mqtt_dev = self._mqtt_devices.get(device_id)
        if mqtt_dev:
            try:
                mqtt_dev.publish_state()
            except Exception:
                log.exception("Fallo publicando estado MQTT de %s", device_id)

    # --------------------------------------------------- API para otros plugins
    # Contrato generico de device_registry.py -- ver TuyaPlugin.get_handle
    # para la explicacion completa de por que es asi en vez de un metodo
    # por capacidad.

    def get_handle(self, capability: str, device_id: str, index: int = 0):
        """Punto de entrada para consumo INTERNO desde otro plugin (hoy
        Lighting) -- control DIRECTO de una bombilla TP-Link, sin pasar
        por HA/MQTT. `index` se ignora (un dispositivo TP-Link expone
        como mucho una luz por `device_id`). TP-Link solo ofrece "light"
        hoy (no todos sus dispositivos son luces, ver `list_actuators``)
        -- cualquier otra capacidad devuelve None, igual que un
        device_id desconocido o que no sea una luz."""
        if capability != "light":
            return None
        return self._manager.light_handle(device_id)

    def list_actuators(self, capability: str) -> list[dict]:
        """Un `{"ref", "name", "brand"}` por cada dispositivo dado de
        alta que resulte ser una luz -- lo que el registro compartido
        agrega para que el selector de la interfaz de Lighting los
        ofrezca sin que el usuario tenga que escribir `tplink:<id>` a
        mano. TP-Link solo ofrece "light" hoy."""
        if capability != "light":
            return []
        out = []
        for device in tplink_store.load_devices():
            device_id = device["id"]
            if self._manager.light_handle(device_id) is None:
                continue
            cfg = device["config"]
            out.append({
                "ref": f"tplink:{device_id}",
                "name": cfg.get("name") or cfg.get("host") or device_id,
                "brand": "TP-Link",
            })
        return out
