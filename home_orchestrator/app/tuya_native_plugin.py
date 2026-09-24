"""Plugin Tuya nuevo (nativo, reimplementa la API de la app). Publica cada
dispositivo como entidad HA NATIVA por MQTT Discovery y traduce los comandos de
HA a DPs (control generico) o a encoders de kit (SweeperKit). No usa la API IoT
de desarrollador ni librerias pip: firma como la app (HMAC-SHA256 con K).

Secretos (app_secret, bmp_secret, cert, sesion, local_keys) viven en la config
del addon (/data), NUNCA en el repo.
"""
from __future__ import annotations
import base64, json, logging, threading, time
import config_store, ha_mqtt, device_registry
from plugin_base import Plugin
from flask import Flask, jsonify, request
from tuya_native import client as tclient, mqtt_transport, auth as tauth, device_manager as tdm, migration as tmig
from tuya_native import handles as thandles

log = logging.getLogger("tuya_native")
PLUGIN_KEY = "tuya"   # EVOLUCION: sustituye al plugin Tuya antiguo (mismo slug/seccion)
STATE_INTERVAL = 30  # s


def _section() -> dict:
    return config_store.read_plugin_section(PLUGIN_KEY, {"auth": {}, "devices": []})


def _persist_auth(fields: dict) -> None:
    """Fusiona claves en la subseccion auth (sesion tras login, sin tocar
    device list ni secretos ya presentes que no vengan en `fields`)."""
    sec = _section()
    auth = dict(sec.get("auth") or {})
    auth.update({k: v for k, v in fields.items() if v is not None})
    sec["auth"] = auth
    config_store.update_plugin_section(PLUGIN_KEY, sec)


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
    # Re-auth automatica: se instala como callback perezoso en el cliente. NO se
    # hace login ansioso (reutiliza el sid/ecode persistidos); solo se re-loguea
    # cuando una llamada devuelve un error de sesion (client.call -> on_session_error).
    # QR no puede renovarse sin re-escanear -> sin callback.
    if a.get("mode") == "app_password" and a.get("email") and a.get("password"):
        email = a["email"]; password = a["password"]; cc = str(a.get("country_code", "34"))
        def _relogin():
            user = tauth.login_email_password(c, email, password, country_code=cc)
            _persist_auth({"sid": user.get("sid"), "ecode": user.get("ecode"), "uid": user.get("uid")})
        c.on_session_error = _relogin
        # Si no hay sesion persistida todavia, logear una vez ahora.
        if not a.get("sid"):
            try: _relogin()
            except Exception: log.exception("tuya: login inicial (password) fallido")
    return c


class TuyaNativePlugin(Plugin):
    slug = "tuya"; name = "Tuya"; version = "1.0.0"   # evolucion del plugin Tuya (reimplementa la app)
    serves_root = True

    def __init__(self):
        self._mqtt = ha_mqtt.HAMqttClient(client_id="home_orchestrator_tuya_native")
        self._devices = {}       # device_id -> TuyaDevice
        self._qr = None          # {token, country_code} del login QR en curso
        self._app = self._build_flask()
        self._stop = threading.Event()
        # Proveedor de dispositivos para consumo INTERNO (Climate/Lighting) sin
        # MQTT -- mismo contrato/prefijo "tuya" que el plugin viejo, para no
        # romper zonas/reglas ya guardadas con refs `tuya:<id>[:<idx>]`.
        device_registry.register_provider("tuya", self)

    # ---------------------------------------------- API para otros plugins
    # Contrato generico de device_registry.py (get_handle/list_actuators),
    # identico al del plugin viejo -- Climate consume "climate", Lighting
    # consume "light". No excluyente con expose_mqtt.
    _DOMAIN_BY_CAPABILITY = {"climate": "climate", "light": "light", "vacuum": "vacuum"}

    def get_handle(self, capability: str, device_id: str, index: int = 0):
        dev = self._devices.get(device_id)
        if dev is None:
            return None
        # Solo se devuelve handle si el dominio del dispositivo casa con la
        # capacidad pedida -- si un ref viejo apunta a un dispositivo de otro
        # tipo, se devuelve None (como el plugin viejo), no un handle mudo (M1).
        if capability == "climate" and dev.plan.get("main_domain") == "climate":
            return thandles.TuyaClimateHandle(dev.ctl, dev.codes)
        if capability == "light" and dev.plan.get("main_domain") == "light":
            return thandles.TuyaLightHandle(dev.ctl, dev.codes)
        return None   # "vacuum" solo se expone via MQTT hoy, sin handle interno

    def get_actuator_history(self, device_id: str, climate_index: int, days: int) -> list[dict]:
        """Capacidad OPCIONAL (thermal_model). El plugin nuevo no mantiene
        historico local todavia -> lista vacia (Climate degrada con gracia:
        aprende de los sensores en vivo, no del historico del actuador)."""
        return []

    def list_actuators(self, capability: str) -> list[dict]:
        domain = self._DOMAIN_BY_CAPABILITY.get(capability)
        if domain is None:
            return []
        out = []
        for dev in self._devices.values():
            if dev.plan.get("main_domain") != domain:
                continue
            out.append({"ref": "tuya:%s" % dev.device_id,
                        "name": dev.name, "brand": "Tuya"})
        return out

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
        sec = self._maybe_migrate(_section()); c = _build_client(sec)
        if not c:
            log.warning("tuya_native sin credenciales configuradas: no se cargan dispositivos")
            return
        a = sec.get("auth") or {}
        mqtt_sess = {"sid": a.get("sid"), "ecode": a.get("ecode"), "uid": a.get("uid"),
                     "device_id": a.get("terminal_device_id")}
        # Se puebla un dict LOCAL y se cambia de golpe al final (no self._devices.clear()
        # a mitad de carga): el bucle de estado y get_handle nunca ven el registro
        # vacio ni un dict mutando bajo sus pies (I2).
        new_devices = {}
        for d in sec.get("devices") or []:
            did = d.get("device_id");
            if not did: continue
            lk = (d.get("local_key") or "").encode() if d.get("local_key") else None
            dev = tdm.TuyaDevice(c, did, d.get("category", ""), d.get("product_id", ""), d.get("name") or did,
                                 local_key=lk, mqtt_transport=mqtt_transport, mqtt_session=mqtt_sess,
                                 expose_advanced=bool(d.get("expose_advanced")))
            # expose_mqtt: publicar o no en HA por MQTT Discovery. NO excluyente
            # con el consumo interno (Climate/Lighting via device_registry): un
            # dispositivo puede consumirse internamente y ademas exponerse en HA,
            # o solo una de las dos cosas. Se cargan TODOS los dispositivos
            # (para poder resolverlos como handle interno); expose_mqtt solo
            # decide la publicacion. Default False (mismo que el plugin viejo).
            dev.expose_mqtt = bool(d.get("expose_mqtt"))
            try:
                dev.refresh_profile(); new_devices[did] = dev
            except Exception:
                log.exception("tuya_native: fallo cargando perfil de %s", did)
        self._devices = new_devices   # cambio atomico (rebind del atributo)

    # --- MQTT discovery + estado + comandos ---
    def _publish_all_discovery(self):
        for dev in list(self._devices.values()):
            if not getattr(dev, "expose_mqtt", False):
                continue   # consumido solo internamente (Climate/Lighting), no va a HA
            for cfg in dev.discovery_configs():
                self._mqtt.publish(cfg["topic"], json.dumps(cfg["payload"]), retain=True)
            self._mqtt.publish("tuya_native/%s/avail" % dev.device_id, "online", retain=True)

    def _publish_all_state(self):
        for dev in list(self._devices.values()):
            if not getattr(dev, "expose_mqtt", False):
                continue
            try:
                for topic, payload in dev.state_messages().items():
                    self._mqtt.publish(topic, json.dumps(payload))
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

    def _reload_async(self):
        """Recarga dispositivos y republica discovery en segundo plano (tras un
        login o cambio de config) sin bloquear la respuesta HTTP."""
        def _job():
            try:
                self._load_devices(); self._publish_all_discovery(); self._publish_all_state()
            except Exception:
                log.exception("tuya_native: recarga tras login/config fallida")
        threading.Thread(target=_job, daemon=True).start()

    def shutdown(self):
        self._stop.set()

    def _login_client(self, sec):
        """Cliente listo para login (creds presentes) SIN exigir sesion todavia.
        None si faltan las credenciales de la app (app_secret/bmp/cert)."""
        return _build_client(sec)

    def _build_flask(self):
        import os
        tpl = os.path.join(os.path.dirname(__file__), "tuya_native_templates")
        app = Flask("tuya_native_plugin", template_folder=tpl)

        @app.get("/")
        def index():
            from flask import render_template
            return render_template("index.html")

        @app.get("/api/status")
        def status():
            sec = _section(); a = sec.get("auth") or {}
            return jsonify({"plugin": self.version, "auth_mode": a.get("mode"),
                            "session_ready": bool(a.get("sid")),
                            "country_code": a.get("country_code"),
                            "email": a.get("email"),
                            "creds_ready": bool(a.get("app_id") and a.get("app_secret")
                                                and a.get("bmp_secret_hex") and a.get("cert_der_b64")),
                            "n_devices": len(sec.get("devices") or [])})

        # ------------------------------------------------------------- LOGIN
        @app.post("/api/login/password")
        def login_password():
            body = request.json or {}
            email = body.get("email"); password = body.get("password")
            cc = str(body.get("country_code") or "34")
            if not (email and password):
                return jsonify({"ok": False, "error": "email y password requeridos"}), 400
            sec = _section(); c = self._login_client(sec)
            if c is None:
                return jsonify({"ok": False, "error": "faltan credenciales de la app (app_secret/bmp/cert)"}), 400
            try:
                user = tauth.login_email_password(c, email, password, country_code=cc)
            except Exception as e:
                log.exception("login password fallido")
                return jsonify({"ok": False, "error": str(e)}), 400
            # Guardar sesion + guardar email/password para re-auth automatica (como la app)
            _persist_auth({"mode": "app_password", "email": email, "password": password,
                           "country_code": cc, "sid": user.get("sid"), "ecode": user.get("ecode"),
                           "uid": user.get("uid")})
            self._reload_async()
            return jsonify({"ok": True, "uid": user.get("uid")})

        @app.post("/api/login/qr/start")
        def login_qr_start():
            sec = _section(); c = self._login_client(sec)
            if c is None:
                return jsonify({"ok": False, "error": "faltan credenciales de la app"}), 400
            cc = (sec.get("auth") or {}).get("country_code")
            try:
                token = tauth.qr_create_token(c, country_code=cc)
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)}), 400
            self._qr = {"token": token, "country_code": cc}
            return jsonify({"ok": True, "token": token, "url": tauth.qr_login_url(token)})

        @app.get("/api/login/qr/poll")
        def login_qr_poll():
            if not self._qr:
                return jsonify({"ok": False, "error": "no hay login QR en curso"}), 400
            sec = _section(); c = self._login_client(sec)
            try:
                user = tauth.qr_poll(c, self._qr["token"], country_code=self._qr.get("country_code"))
            except Exception as e:
                self._qr = None
                return jsonify({"ok": False, "status": "error", "error": str(e)}), 400
            if not user:
                return jsonify({"ok": True, "status": "pending"})
            _persist_auth({"mode": "app_qr", "sid": user.get("sid"), "ecode": user.get("ecode"),
                           "uid": user.get("uid")})
            self._qr = None
            self._reload_async()
            return jsonify({"ok": True, "status": "ok", "uid": user.get("uid")})
        @app.get("/api/devices")
        def devices():
            return jsonify([{"device_id": d.device_id, "name": d.name, "category": d.category,
                             "domain": d.plan.get("main_domain"),
                             "expose_mqtt": getattr(d, "expose_mqtt", False),
                             "expose_advanced": d.expose_advanced,
                             "entities": d.plan.get("n_entities")} for d in self._devices.values()])
        @app.post("/api/device/<did>/flags")
        def flags(did):
            """Actualiza expose_mqtt / expose_advanced de un dispositivo. Solo se
            tocan las claves presentes en el cuerpo (toggles independientes)."""
            body = request.json or {}
            sec = _section()
            for d in sec.get("devices") or []:
                if d.get("device_id") != did:
                    continue
                if "expose_mqtt" in body:
                    d["expose_mqtt"] = bool(body["expose_mqtt"])
                if "expose_advanced" in body:
                    d["expose_advanced"] = bool(body["expose_advanced"])
            config_store.update_plugin_section(PLUGIN_KEY, sec)
            self._load_devices()
            # Si se acaba de DESACTIVAR expose_mqtt hay que retirar el discovery
            # ya publicado (retenido) para que la entidad desaparezca de HA.
            self._republish_discovery(did)
            return jsonify({"ok": True})
        return app

    def _republish_discovery(self, changed_did: str | None = None):
        """Republica discovery de los expuestos y RETIRA (payload vacio) el de un
        dispositivo que ya no se expone en HA."""
        if changed_did is not None:
            dev = self._devices.get(changed_did)
            if dev is not None and not getattr(dev, "expose_mqtt", False):
                for cfg in dev.discovery_configs():
                    self._mqtt.publish(cfg["topic"], "", retain=True)  # borra la entidad retenida
                self._mqtt.publish("tuya_native/%s/avail" % changed_did, "offline", retain=True)
        self._publish_all_discovery()
