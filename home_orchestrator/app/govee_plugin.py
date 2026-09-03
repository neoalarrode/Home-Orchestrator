"""
Plugin de Govee para el nucleo Home Orchestrator -- puro puente de
ingesta, mismo papel que TuyaPlugin/TplinkPlugin pero hablando el
protocolo LAN de Govee directamente (ver govee/device_manager.py: no hay
libreria de terceros en Python para esto, a diferencia de `python-kasa`
para TP-Link). LAN sigue siendo la via PRIMARIA; la API Cloud oficial de
Govee (ver govee_cloud.py) es un respaldo opcional para lo que LAN no
puede alcanzar -- a peticion expresa del usuario, no todos los
dispositivos soportan LAN.

Dos formas de usar un dispositivo dado de alta, no excluyentes (mismo
criterio que Tuya/TP-Link):
  - Consumo INTERNO: Lighting puede pedir un `GoveeLightHandle` (ver
    light_handle()) y controlar la bombilla EN EL MISMO PROCESO, sin
    pasar por Home Assistant.
  - Exposicion opcional a HA por MQTT Discovery (`expose_mqtt` por
    dispositivo, ver govee/mqtt_govee.py).

Descubrimiento: broadcast multicast bajo demanda (`/api/discover`, ver
GoveeDeviceManager.discover) -- igual que TP-Link, no un listener de
fondo persistente.
"""

from __future__ import annotations

import logging
import threading

import flask

import ha_mqtt
import govee_cloud
import govee_store
from govee.device_manager import GoveeDeviceManager
from govee.mqtt_govee import MqttGoveeDevice
from plugin_base import Plugin

log = logging.getLogger("govee_plugin")


class GoveePlugin(Plugin):
    slug = "govee"
    name = "Govee Orchestrator"
    version = "0.2.2"

    def __init__(self) -> None:
        self._manager = GoveeDeviceManager(on_any_change=self._on_device_change)
        self._mqtt = ha_mqtt.HAMqttClient(client_id="home_orchestrator_govee")
        self._mqtt_devices: dict[str, MqttGoveeDevice] = {}
        self._app = flask.Flask("govee_plugin", template_folder="govee_templates")
        self._register_routes()

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
            devices = govee_store.load_devices()
            out = []
            for d in devices:
                item = {"id": d["id"], "config": d["config"]}
                handle = self._manager.light_handle(d["id"])
                if handle is not None:
                    item["live"] = {
                        "connected": handle.available,
                        "is_on": handle.is_on,
                        "brightness_pct": handle.brightness_pct,
                        "color_temp_kelvin": handle.color_temp_kelvin,
                    }
                out.append(item)
            return flask.jsonify(out)

        @app.post("/api/devices")
        def _add_device():
            payload = flask.request.get_json(force=True) or {}
            device = govee_store.add_device(payload)
            self._start_device(device)
            return flask.jsonify(device), 201

        @app.put("/api/devices/<device_id>")
        def _update_device(device_id):
            payload = flask.request.get_json(force=True) or {}
            device = govee_store.update_device(device_id, payload)
            if not device:
                return flask.jsonify({"error": "dispositivo no encontrado"}), 404
            self._stop_device(device_id)
            self._start_device(device)
            return flask.jsonify(device)

        @app.delete("/api/devices/<device_id>")
        def _delete_device(device_id):
            self._stop_device(device_id)
            ok = govee_store.delete_device(device_id)
            return flask.jsonify({"deleted": ok})

        @app.get("/api/status")
        def _status():
            return flask.jsonify({
                "version": self.version,
                "devices": len(govee_store.load_devices()),
                "mqtt_connected": self._mqtt.connected,
            })

        # ------------------------------------------------ descubrimiento -
        # Escaneo ACTIVO bajo demanda (ver docstring del modulo) -- nunca
        # añade nada por su cuenta, solo enseña lo que ha respondido.

        @app.post("/api/discover")
        def _discover():
            added_ips = {d["config"]["host"] for d in govee_store.load_devices()}
            try:
                found = self._manager.discover()
            except Exception:
                log.exception("Fallo escaneando la LAN en busca de dispositivos Govee")
                return flask.jsonify({"error": "fallo escaneando la LAN"}), 502
            out = [
                {
                    "host": info["ip"],
                    "sku": info.get("sku"),
                    "device": info.get("device"),
                    "already_added": info["ip"] in added_ips,
                }
                for info in found
            ]
            return flask.jsonify(out)

        # ------------------------------------------------ cuenta / cloud -
        # API Cloud oficial de Govee (ver govee_cloud.py) -- SOLO como
        # respaldo de lo que LAN no puede alcanzar (mismo criterio ya
        # aplicado a EcoFlow Cloud/BLE en Energy Orchestrator). La API key
        # es de cuenta, no por dispositivo -- un unico campo, igual que el
        # patron ya usado en tplink_store.py.

        @app.get("/api/account")
        def _get_account():
            account = govee_store.load_account()
            return flask.jsonify({"api_key_set": bool(account.get("api_key"))})

        @app.post("/api/account")
        def _save_account():
            payload = flask.request.get_json(force=True) or {}
            govee_store.save_account({"api_key": payload.get("api_key", "")})
            self._manager.set_cloud_api_key(govee_store.load_account().get("api_key"))
            return flask.jsonify({"ok": True})

        @app.post("/api/discover_cloud")
        def _discover_cloud():
            api_key = govee_store.load_account().get("api_key")
            if not api_key:
                return flask.jsonify({"error": "no hay API key de Govee configurada"}), 400
            devices = govee_cloud.list_devices(api_key)
            if devices is None:
                return flask.jsonify({"error": "la API de Govee no respondio"}), 502
            added_macs = {
                d["config"]["govee_device_mac"] for d in govee_store.load_devices()
                if d["config"].get("govee_device_mac")
            }
            out = [
                {
                    "device": d.get("device"),
                    "sku": d.get("model"),
                    "name": d.get("deviceName"),
                    "already_added": d.get("device") in added_macs,
                }
                for d in devices
            ]
            return flask.jsonify(out)

    # ------------------------------------------------------------- arranque

    def start_background_threads(self) -> None:
        # BUG REAL, confirmado en produccion (crash-loop entero del addon,
        # ver core_app.py): el puerto UDP 4002 (fijo, del propio protocolo
        # LAN de Govee -- ver device_manager.py) puede estar ya tomado por
        # OTRO proceso del host (`host_network: true` en config.yaml, asi
        # que esto compite por puertos con TODO el host, no solo con este
        # addon). Un `OSError` aqui ya no tira el proceso ENTERO abajo
        # (core_app.py tambien lo protege de forma generica, por si otro
        # plugin futuro falla igual), pero se atrapa TAMBIEN aqui para que
        # el resto de este plugin (la API de dispositivos, MQTT) siga
        # funcionando con normalidad -- solo el listener LAN de Govee se
        # queda sin arrancar, `connected()` ya reporta False con
        # normalidad para cualquier dispositivo mientras tanto.
        try:
            self._manager.start()
        except OSError:
            log.exception(
                "Govee: fallo arrancando el listener UDP (puerto %d) -- revisa si otro "
                "proceso del host ya lo tiene tomado. Las bombillas se quedaran sin "
                "conexion hasta que se resuelva.", 4002,
            )
        self._manager.set_cloud_api_key(govee_store.load_account().get("api_key"))
        self._mqtt.connect()
        devices = govee_store.load_devices()
        for device in devices:
            self._start_device(device)
        log.info("Plugin Govee arrancado con %d dispositivo(s)", len(devices))

    def _start_device(self, device: dict) -> None:
        cfg = device["config"]
        self._manager.set_cloud_identity(device["id"], cfg.get("govee_device_mac"), cfg.get("govee_sku"))
        has_cloud_identity = bool(cfg.get("govee_device_mac") and cfg.get("govee_sku"))
        if cfg.get("host"):
            # Mismo patron ya corregido en Shelly/Tuya/TP-Link: un fallo dando de
            # alta UN dispositivo (poco probable aqui, `add_device` solo hace un
            # `sendto` UDP ya protegido, pero cualquier error inesperado) no debe
            # abortar el bucle de `start_background_threads` y dejar sin arrancar
            # al resto de dispositivos Govee.
            try:
                self._manager.add_device(device["id"], cfg["host"])
            except Exception:
                log.exception("Fallo dando de alta el dispositivo Govee '%s'", cfg.get("name") or cfg["host"])
        elif not has_cloud_identity:
            # Sin host LAN NI identidad de cuenta -- no hay forma de
            # controlar este dispositivo por ningun camino.
            log.warning("Dispositivo Govee '%s' sin host ni identidad cloud -- no se puede controlar", cfg.get("name") or device["id"])
            return

        # BUG REAL, corregido antes de llegar a produccion: esto vivia
        # DENTRO del bloque `if cfg.get("host")` de arriba -- un
        # dispositivo dado de alta SOLO por la nube (sin host LAN) nunca
        # llegaba a esta comprobacion, asi que `expose_mqtt` no hacia
        # nada para el (la bombilla quedaba controlable desde Lighting
        # pero invisible en Home Assistant). LAN o cloud, la exposicion a
        # HA es una decision independiente de por donde se controla.
        if cfg.get("expose_mqtt"):
            mqtt_dev = MqttGoveeDevice(self._mqtt, self._manager, device["id"], cfg.get("name") or cfg.get("host") or device["id"])
            mqtt_dev.publish_discovery()
            # Mismo bug ya corregido en Tuya/TP-Link que se evita desde el
            # principio aqui: sin este publish_state() inicial, la
            # entidad recien expuesta se queda en "unknown" hasta que
            # llegue el primer `devStatus` real del dispositivo.
            mqtt_dev.publish_state()
            self._mqtt_devices[device["id"]] = mqtt_dev

    def _stop_device(self, device_id: str) -> None:
        mqtt_dev = self._mqtt_devices.pop(device_id, None)
        if mqtt_dev:
            mqtt_dev.remove_discovery()
        self._manager.remove_device(device_id)
        self._manager.set_cloud_identity(device_id, None, None)

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
        """Punto de entrada para consumo INTERNO desde Lighting -- control
        DIRECTO de una bombilla Govee, sin pasar por HA/MQTT. `index` se
        ignora (un dispositivo Govee expone como mucho una luz por
        `device_id`). Govee solo ofrece "light" hoy -- cualquier otra
        capacidad devuelve None, igual que un device_id desconocido."""
        if capability != "light":
            return None
        return self._manager.light_handle(device_id)

    def list_actuators(self, capability: str) -> list[dict]:
        """Un `{"ref", "name", "brand"}` por cada dispositivo dado de alta
        -- lo que el registro compartido agrega para que el selector de
        la interfaz de Lighting los ofrezca sin que el usuario tenga que
        escribir `govee:<id>` a mano. Govee solo ofrece "light" hoy."""
        if capability != "light":
            return []
        out = []
        for device in govee_store.load_devices():
            device_id = device["id"]
            cfg = device["config"]
            out.append({
                "ref": f"govee:{device_id}",
                "name": cfg.get("name") or cfg.get("host") or device_id,
                "brand": "Govee",
            })
        return out
