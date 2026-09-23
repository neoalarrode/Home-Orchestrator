"""Plugin Tuya nuevo (nativo, reimplementa la API de la app). Publica cada
dispositivo como entidad HA NATIVA por MQTT Discovery y traduce los comandos de
HA a DPs (control generico) o a encoders de kit (SweeperKit). No usa la API IoT
de desarrollador ni librerias pip: firma como la app (HMAC-SHA256 con K).

Secretos (app_secret, bmp_secret, cert, sesion, local_keys) viven en la config
del addon (/data), NUNCA en el repo.
"""
from __future__ import annotations
import base64, json, logging, threading, time
import config_store, ha_mqtt
from plugin_base import Plugin
from flask import Flask, jsonify, request
from tuya_native import client as tclient, mqtt_transport, auth as tauth, device_manager as tdm, migration as tmig

log = logging.getLogger("tuya_native")
PLUGIN_KEY = "tuya"   # EVOLUCION: sustituye al plugin Tuya antiguo (mismo slug/seccion)
STATE_INTERVAL = 30  # s


def _section() -> dict:
    return config_store.read_plugin_section(PLUGIN_KEY, {"auth": {}, "devices": []})


def _build_client(sec: dict):
    a = sec.get("auth") or {}
    if not (a.get("app_id") and a.get("app_secret") and a.get("bmp_secret_hex") and a.get("cert_der_b64")):
        return None, None
    creds = tclient.Credentials(
        app_id=a["app_id"], app_secret=a["app_secret"],
        cert_der=base64.b64decode(a["cert_der_b64"]),
        bmp_secret_hex=a["bmp_secret_hex"], chkey=a.get("chkey", ""),
        device_id=a.get("terminal_device_id", ""))
    c = tclient.TuyaMobileClient(creds=creds, session_id=a.get("sid"), ecode=a.get("ecode"))
    # re-auth automatica si hay email/password guardados; si es QR, sin relogin
    if a.get("mode") == "app_password" and a.get("email") and a.get("password"):
        sm = tauth.SessionManager.for_password(c, a["email"], a["password"],
                                               country_code=a.get("country_code", "34"))
    else:
        sm = tauth.SessionManager(c, None)
    return c, sm


class TuyaNativePlugin(Plugin):
    slug = "tuya"; name = "Tuya"; version = "1.0.0"   # evolucion del plugin Tuya (reimplementa la app)

    def __init__(self):
        self._mqtt = ha_mqtt.HAMqttClient(client_id="home_orchestrator_tuya_native")
        self._devices = {}       # device_id -> TuyaDevice
        self._app = self._build_flask()
        self._stop = threading.Event()

    # --- carga de dispositivos ---
    def _maybe_migrate(self, sec):
        """Si la seccion esta en el formato ANTIGUO (account IoT o devices con
        subclave 'config'), la migra en caliente al formato nuevo, conservando
        local_key/direccion/nombre. La auth de la app (QR/password) se configura
        aparte; las credenciales IoT viejas se descartan."""
        devs = sec.get("devices") or []
        old = ("account" in sec) or any(isinstance(d, dict) and "config" in d for d in devs)
        if not old:
            return sec
        new = tmig.migrate_section(sec)               # devices planos + auth stub
        new_auth = dict(sec.get("auth") or {})         # preservar auth de app ya configurada
        merged = new.get("auth") or {}; merged.update({k: v for k, v in new_auth.items() if v})
        new["auth"] = merged
        config_store.update_plugin_section(PLUGIN_KEY, new)
        log.info("tuya: config antigua migrada al formato nuevo (%d dispositivos)", len(new.get("devices") or []))
        return new

    def _load_devices(self):
        sec = self._maybe_migrate(_section()); c, sm = _build_client(sec)
        if not c:
            log.warning("tuya_native sin credenciales configuradas: no se cargan dispositivos")
            return
        a = sec.get("auth") or {}
        mqtt_sess = {"sid": a.get("sid"), "ecode": a.get("ecode"), "uid": a.get("uid"),
                     "device_id": a.get("terminal_device_id")}
        self._devices.clear()
        for d in sec.get("devices") or []:
            did = d.get("device_id");
            if not did: continue
            lk = (d.get("local_key") or "").encode() if d.get("local_key") else None
            dev = tdm.TuyaDevice(c, did, d.get("category", ""), d.get("product_id", ""), d.get("name") or did,
                                 local_key=lk, mqtt_transport=mqtt_transport, mqtt_session=mqtt_sess,
                                 expose_advanced=bool(d.get("expose_advanced")))
            try:
                dev.refresh_profile(); self._devices[did] = dev
            except Exception:
                log.exception("tuya_native: fallo cargando perfil de %s", did)

    # --- MQTT discovery + estado + comandos ---
    def _publish_all_discovery(self):
        for dev in self._devices.values():
            for cfg in dev.discovery_configs():
                self._mqtt.publish(cfg["topic"], json.dumps(cfg["payload"]), retain=True)
            self._mqtt.publish("tuya_native/%s/avail" % dev.device_id, "online", retain=True)

    def _publish_all_state(self):
        for dev in self._devices.values():
            try:
                self._mqtt.publish("tuya_native/%s/state" % dev.device_id, json.dumps(dev.state_payload()))
            except Exception:
                log.debug("estado %s no disponible", dev.device_id)

    def _on_command(self, topic, payload):
        # topic: tuya_native/<devId>/cmd[/<code>]  o  .../set (light json)
        try:
            parts = topic.split("/"); did = parts[1]
            dev = self._devices.get(did)
            if not dev: return
            dom = dev.plan.get("main_domain")
            sub = parts[3] if len(parts) > 3 else None
            data = payload.decode() if isinstance(payload, (bytes, bytearray)) else payload
            try: data = json.loads(data)
            except Exception: pass
            if dom == "light" and (parts[2] == "set"):
                dev.handle_command("light", "set", data)
            elif dom == "vacuum":
                dev.handle_command("vacuum", sub or data, data)
            else:
                dev.handle_command(dom, sub, data)  # generico: sub=code
        except Exception:
            log.exception("tuya_native: comando fallido en %s", topic)

    # --- Plugin API ---
    def flask_app(self): return self._app

    def start_background_threads(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        self._load_devices()
        if not self._mqtt.connect():
            log.error("tuya_native: no se pudo conectar al broker MQTT de HA"); return
        self._publish_all_discovery()
        self._mqtt.subscribe("tuya_native/+/cmd/#", lambda t, p: self._on_command(t, p))
        self._mqtt.subscribe("tuya_native/+/set", lambda t, p: self._on_command(t, p))
        while not self._stop.is_set():
            self._publish_all_state(); self._stop.wait(STATE_INTERVAL)

    def shutdown(self):
        self._stop.set()

    def _build_flask(self):
        app = Flask(__name__)
        @app.get("/api/status")
        def status():
            sec = _section(); a = sec.get("auth") or {}
            return jsonify({"plugin": self.version, "auth_mode": a.get("mode"),
                            "session_ready": bool(a.get("sid")), "n_devices": len(sec.get("devices") or [])})
        @app.get("/api/devices")
        def devices():
            return jsonify([{"device_id": d.device_id, "name": d.name, "category": d.category,
                             "domain": d.plan.get("main_domain"), "advanced": d.expose_advanced,
                             "entities": d.plan.get("n_entities")} for d in self._devices.values()])
        @app.post("/api/device/<did>/advanced")
        def advanced(did):
            on = bool((request.json or {}).get("expose_advanced"))
            sec = _section()
            for d in sec.get("devices") or []:
                if d.get("device_id") == did: d["expose_advanced"] = on
            config_store.update_plugin_section(PLUGIN_KEY, sec)
            self._load_devices(); self._publish_all_discovery()
            return jsonify({"ok": True, "expose_advanced": on})
        return app
