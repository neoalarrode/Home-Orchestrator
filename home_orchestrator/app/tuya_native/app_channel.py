"""Canal MQTT de la app, PERMANENTE y en los dos sentidos.

Hasta ahora el plugin solo abria una conexion de usar y tirar para MANDAR
(`DeviceController.publish_message`) y nunca escuchaba: lo que el robot contesta
o lo que cambias desde la app del movil (dividir una habitacion, renombrarla...)
no llegaba nunca. Aqui se mantiene UNA conexion abierta al broker de Tuya,
suscrita a los dos temas de cada dispositivo registrado:

  smart/mb/in/<devId>    robot -> app   (protocolo 65: respuestas y avisos)
  smart/mb/out/<devId>   app   -> robot (protocolo 64: lo que manda CUALQUIER
                                         app de la cuenta, tambien la del movil)

VERIFICADO en vivo (Conga X80): el robot contesta por `in` a `devInfoQry`,
`quietHoursQry` y `scheduleQry` con el mismo `taskId` de la peticion.

Un solo cliente por cuenta: el `client_id` sale de la cuenta, asi que dos
conexiones a la vez se echan la una a la otra. Por eso los envios pasan tambien
por aqui (ver `DeviceController.publish_message`).
"""
from __future__ import annotations
import json, logging, ssl, threading, time
from . import mqtt_transport

log = logging.getLogger("tuya_native")

PROTOCOL_ROBOT_TO_APP = 65
REQUEST_TIMEOUT_SECONDS = 10.0   # el mismo plazo que usa la app


class AppChannel:
    def __init__(self, session_provider, transport=mqtt_transport):
        """`session_provider()` -> {"sid","ecode","uid","device_id"} ACTUAL (la
        sesion puede renovarse sola; se vuelve a leer en cada reconexion)."""
        self._session_provider = session_provider
        self._t = transport
        self._lock = threading.Lock()
        self._devices: dict[str, dict] = {}      # devId -> {"key": bytes, "cb": callable|None}
        self._pending: dict[str, dict] = {}      # taskId -> {"event": Event, "reply": dict|None}
        self._client = None
        self._connected = threading.Event()
        self._paused = False

    # ------------------------------------------------------------ registro
    def register(self, device_id: str, local_key: bytes, on_message=None) -> None:
        with self._lock:
            self._devices[device_id] = {"key": local_key, "cb": on_message}
        if self.connected:
            self._subscribe(device_id)

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    # --------------------------------------------------------- ciclo de vida
    def _credentials(self):
        s = self._session_provider() or {}
        if not (s.get("sid") and s.get("ecode") and s.get("uid")):
            return None
        return self._t.derive_credentials(s["sid"], s["ecode"], s["uid"], s.get("device_id"))

    def start(self) -> bool:
        cr = self._credentials()
        if cr is None:
            return False
        import paho.mqtt.client as mqtt
        try:
            cl = mqtt.Client(client_id=cr.client_id, protocol=mqtt.MQTTv311,
                             callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        except (AttributeError, TypeError):   # paho < 2
            cl = mqtt.Client(client_id=cr.client_id, protocol=mqtt.MQTTv311)
        cl.username_pw_set(cr.username, cr.password)
        cl.tls_set(cert_reqs=ssl.CERT_NONE); cl.tls_insecure_set(True)
        cl.on_connect = self._on_connect
        cl.on_disconnect = self._on_disconnect
        cl.on_message = self._on_message
        cl.reconnect_delay_set(min_delay=2, max_delay=60)
        self._client = cl
        self._paused = False
        try:
            cl.connect_async(cr.host, cr.port, 60)
            cl.loop_start()
        except Exception:
            log.exception("tuya_native: no se pudo abrir el canal de la app")
            return False
        return True

    def stop(self) -> None:
        cl, self._client = self._client, None
        self._connected.clear()
        if cl is not None:
            try:
                cl.loop_stop(); cl.disconnect()
            except Exception:
                pass

    def pause(self) -> None:
        """Suelta la conexion un momento (para un envio de usar y tirar con el
        mismo client_id). `resume()` la retoma."""
        self._paused = True
        self.stop()

    def resume(self) -> None:
        if self._paused:
            self.start()

    # ------------------------------------------------------------ callbacks
    def _subscribe(self, device_id: str) -> None:
        cl = self._client
        if cl is None:
            return
        cl.subscribe(self._t.TOPIC_SUB + device_id)
        cl.subscribe(self._t.TOPIC_PUB + device_id)

    def _on_connect(self, client, userdata, flags, rc, *extra):
        code = getattr(rc, "value", rc)
        if code != 0:
            log.warning("tuya_native: canal de la app rechazado (rc=%s)", rc)
            return
        self._connected.set()
        with self._lock:
            ids = list(self._devices)
        for did in ids:
            self._subscribe(did)
        log.info("tuya_native: canal de la app conectado (%d dispositivo(s) a la escucha)", len(ids))

    def _on_disconnect(self, client, userdata, *extra):
        self._connected.clear()
        # La sesion puede haberse renovado: las credenciales de la proxima
        # reconexion se leen otra vez.
        try:
            cr = self._credentials()
            if cr is not None:
                client.username_pw_set(cr.username, cr.password)
        except Exception:
            log.debug("tuya_native: no se pudieron releer las credenciales del canal", exc_info=True)

    def _on_message(self, client, userdata, msg):
        try:
            direction, device_id = ("in", msg.topic[len(self._t.TOPIC_SUB):]) if msg.topic.startswith(self._t.TOPIC_SUB) \
                else ("out", msg.topic[len(self._t.TOPIC_PUB):])
            with self._lock:
                dev = self._devices.get(device_id)
            if dev is None:
                return
            raw = self._t.decrypt_frame(msg.payload, dev["key"])
            if raw is None:
                return
            doc = json.loads(raw)
            data = doc.get("data")
            if not isinstance(data, dict):
                return
            if direction == "in":
                task_id = str(data.get("taskId") or "")
                with self._lock:
                    waiter = self._pending.get(task_id)
                if waiter is not None:
                    waiter["reply"] = data
                    waiter["event"].set()
            cb = dev.get("cb")
            if cb is not None:
                cb(direction, data)
        except Exception:
            log.exception("tuya_native: fallo procesando un mensaje del canal de la app")

    # --------------------------------------------------------------- envios
    def publish(self, device_id: str, message: dict, *, protocol: int = mqtt_transport.PROTOCOL_APP_TO_ROBOT) -> bool:
        with self._lock:
            dev = self._devices.get(device_id)
        cl = self._client
        if dev is None or cl is None or not self._connected.wait(5.0):
            return False
        frame, _, _ = self._t.build_frame(message, dev["key"], protocol=protocol)
        info = cl.publish(self._t.TOPIC_PUB + device_id, frame)
        try:
            info.wait_for_publish(5.0)
        except Exception:
            pass
        return getattr(info, "rc", 0) == 0

    def request(self, device_id: str, req_type: str, message: dict | None = None,
                *, timeout: float = REQUEST_TIMEOUT_SECONDS, version: str = "1.0.0") -> dict | None:
        """Pregunta al dispositivo y espera SU respuesta (mismo `taskId`). None si
        no contesta a tiempo o no hay conexion."""
        task_id = str(int(time.time() * 1000))
        waiter = {"event": threading.Event(), "reply": None}
        with self._lock:
            self._pending[task_id] = waiter
        try:
            body = {"reqType": req_type, "version": version, "taskId": task_id}
            body.update(message or {})
            if not self.publish(device_id, body):
                return None
            waiter["event"].wait(timeout)
            return waiter["reply"]
        finally:
            with self._lock:
                self._pending.pop(task_id, None)
