"""
Puente LAN con bombillas Govee -- protocolo UDP no oficial pero bien
documentado por la comunidad (el mismo que usa la via "LAN" de
govee2mqtt de wez, ver https://github.com/wez/govee2mqtt/blob/main/docs/
LAN.md, y proyectos hermanos como govee-lan-hass) -- via PRIMARIA, pero
ya no la unica: un dispositivo sin la "Govee LAN API" activada en la app
oficial (ajuste por dispositivo), o un modelo que no la soporte en
absoluto, cae al respaldo de la API Cloud oficial (ver govee_cloud.py,
`_cloud_status`/`_cloud_control` mas abajo) -- a peticion expresa del
usuario. govee2mqtt en si combina TRES canales (LAN, AWS IoT con
email/contraseña de la cuenta -- protocolo NO documentado --, y esta
misma API REST oficial); aqui SOLO se implementa LAN + la API REST
documentada, nunca el canal AWS IoT no documentado -- mismo criterio de
"sin cajas negras" aplicado al resto de Home Orchestrator, con lo que SI
hay alternativa documentada.

Puertos (fijos, del propio protocolo, no configurables):
  - 4001: el dispositivo ESCUCHA aqui el mensaje de "scan" (enviado por
    multicast a 239.255.255.250).
  - 4002: el CLIENTE escucha aqui -- el dispositivo responde tanto al
    scan como a cualquier consulta de estado a este puerto, por unicast.
  - 4003: el dispositivo ESCUCHA aqui los comandos de control (turn/
    brightness/colorwc) y las consultas de estado (devStatus), enviados
    por unicast (el multicast solo hace falta para el descubrimiento).

A diferencia de TP-Link (donde `python-kasa` ya habla el protocolo real)
aqui no hay libreria de terceros en Python -- se reimplementa el JSON
crudo, mismo espiritu que `tuya/tuya_lan.py`. A diferencia de Tuya (LAN
con PUSH real de cambios), Govee tampoco empuja nada por su cuenta salvo
en respuesta a un `devStatus` -- de ahi el sondeo periodico, mismo
patron que `tplink/device_manager.py`.
"""

from __future__ import annotations

import colorsys
import json
import logging
import socket
import threading
import time
from typing import Callable

import govee_cloud

log = logging.getLogger("govee.device_manager")

MULTICAST_IP = "239.255.255.250"
SCAN_PORT = 4001
LISTEN_PORT = 4002
CONTROL_PORT = 4003

POLL_INTERVAL_SECONDS = 5
STALE_AFTER_SECONDS = 20  # sin devStatus en este margen, se considera "sin conexion"
SCAN_WINDOW_SECONDS = 4

# Cuanto se reutiliza un estado leido de la nube antes de volver a
# preguntar -- a diferencia de LAN (que empuja `devStatus` cada
# `POLL_INTERVAL_SECONDS` sin coste real, LAN), cada lectura de la API
# Cloud cuenta contra el limite diario de la cuenta (ver govee_cloud.py),
# asi que aqui se cachea mas tiempo -- el respaldo cloud es para
# dispositivos que LAN no puede alcanzar, no para tiempo real.
CLOUD_STATE_CACHE_SECONDS = 20

MIN_KELVIN, MAX_KELVIN = 2000, 9000


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _hs_to_rgb(hue: float, sat: float) -> tuple[int, int, int]:
    """HS (grados/porcentaje, escala de HA) -> RGB 0-255 con V=100 fijo --
    el propio brillo de Govee se manda por separado (`brightness`), igual
    que `light.hs_color` de HA nunca mezcla el value de HSV con el
    brillo, son dos campos independientes."""
    r, g, b = colorsys.hsv_to_rgb(hue / 360, sat / 100, 1.0)
    return round(r * 255), round(g * 255), round(b * 255)


class GoveeDeviceManager:
    """`on_any_change(device_id)`, si se da, se llama desde el hilo
    receptor cada vez que llega un `devStatus` de un dispositivo dado de
    alta -- mismo contrato simple que `TplinkDeviceManager`/
    `TuyaDeviceManager` (no hay forma de distinguir "cambio de verdad" de
    "sondeo sin novedad", asi que se avisa siempre que responde)."""

    def __init__(self, on_any_change: Callable[[str], None] | None = None) -> None:
        self._on_any_change = on_any_change
        self._lock = threading.RLock()
        self._devices: dict[str, dict] = {}  # device_id -> {"ip", "status", "last_seen"}
        self._ip_to_id: dict[str, str] = {}
        self._sock: socket.socket | None = None
        self._scan_lock = threading.Lock()
        # Un dict por escaneo EN CURSO (ver `discover`) -- antes era un unico
        # hueco compartido, y dos escaneos concurrentes se pisaban.
        self._active_scans: list[dict[str, dict]] = []
        # Respaldo CLOUD (ver govee_cloud.py) -- identidad de cuenta
        # (MAC+modelo) por dispositivo dado de alta, y la API key global
        # de la cuenta. Ambos opcionales: sin api_key, o sin identidad
        # cloud para un dispositivo dado, ese dispositivo sencillamente
        # se queda sin respaldo si LAN no responde, como hasta ahora.
        self._cloud_identity: dict[str, tuple[str, str]] = {}  # device_id -> (mac, sku)
        self._cloud_api_key: str | None = None
        self._cloud_state_cache: dict[str, dict] = {}  # mac -> {"status", "fetched_at"}

    # ------------------------------------------------------------ cloud

    def set_cloud_api_key(self, api_key: str | None) -> None:
        self._cloud_api_key = api_key or None

    def set_cloud_identity(self, device_id: str, mac: str | None, sku: str | None) -> None:
        if mac and sku:
            self._cloud_identity[device_id] = (mac, sku)
        else:
            self._cloud_identity.pop(device_id, None)

    def _cloud_status(self, device_id: str, *, fresh: bool = False) -> dict | None:
        """Estado del dispositivo tal y como lo ve la nube, mapeado a los
        MISMOS nombres de campo que ya usa el estado LAN (`onOff`,
        `brightness`, `colorTemInKelvin`) para que `GoveeLightHandle` no
        tenga que saber de donde vino. `None` si no hay identidad cloud
        para este dispositivo, no hay api_key configurada, o la nube no
        responde."""
        identity = self._cloud_identity.get(device_id)
        if identity is None or not self._cloud_api_key:
            return None
        mac, sku = identity
        cached = self._cloud_state_cache.get(mac)
        if not fresh and cached is not None and (time.time() - cached["fetched_at"]) < CLOUD_STATE_CACHE_SECONDS:
            return cached["status"]
        raw = govee_cloud.get_state(self._cloud_api_key, mac, sku)
        if raw is None:
            return cached["status"] if cached is not None else None
        status = {}
        if "powerState" in raw:
            status["onOff"] = 1 if raw["powerState"] == "on" else 0
        if "brightness" in raw:
            status["brightness"] = raw["brightness"]
        if "colorTem" in raw and raw["colorTem"]:
            status["colorTemInKelvin"] = raw["colorTem"]
        self._cloud_state_cache[mac] = {"status": status, "fetched_at": time.time()}
        return status

    def _cloud_control(self, device_id: str, on: bool | None = None, brightness_pct: float | None = None,
                        color_temp_kelvin: float | None = None) -> bool:
        identity = self._cloud_identity.get(device_id)
        if identity is None or not self._cloud_api_key:
            return False
        mac, sku = identity
        ok = True
        if on is not None:
            ok = govee_cloud.control(self._cloud_api_key, mac, sku, "turn", "on" if on else "off") and ok
        if brightness_pct is not None:
            ok = govee_cloud.control(self._cloud_api_key, mac, sku, "brightness", int(_clamp(round(brightness_pct), 1, 100))) and ok
        if color_temp_kelvin is not None:
            kelvin = int(_clamp(round(color_temp_kelvin), MIN_KELVIN, MAX_KELVIN))
            ok = govee_cloud.control(self._cloud_api_key, mac, sku, "colorTem", kelvin) and ok
        if ok:
            # Mismo repintado optimista que la via LAN -- el proximo
            # sondeo (aqui, la siguiente lectura tras CLOUD_STATE_CACHE_SECONDS)
            # ya confirma el valor real.
            cached = self._cloud_state_cache.get(mac, {}).get("status", {})
            status = dict(cached)
            if on is not None:
                status["onOff"] = 1 if on else 0
            if brightness_pct is not None:
                status["brightness"] = round(brightness_pct)
            if color_temp_kelvin is not None:
                status["colorTemInKelvin"] = round(color_temp_kelvin)
            self._cloud_state_cache[mac] = {"status": status, "fetched_at": time.time()}
        return ok

    # ------------------------------------------------------------ arranque

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # SO_REUSEADDR NO evita EADDRINUSE en UDP en Linux (eso es
        # SO_REUSEPORT) -- este puerto lo compite con cualquier proceso del
        # HOST, porque el addon corre con `host_network: true` (ver
        # config.yaml). Sin esto, otro proceso que ya tuviera el 4002 tomado
        # dejaba a Govee sin arrancar del todo. No existe en todas las
        # plataformas, asi que es opcional.
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        try:
            sock.bind(("0.0.0.0", LISTEN_PORT))
        except OSError:
            # El socket se cerraba y se filtraba al propagar el fallo (el
            # llamante atrapa la excepcion, pero el descriptor quedaba abierto
            # para siempre). Se cierra aqui antes de re-lanzar.
            sock.close()
            log.error(
                "Govee: no se pudo abrir el puerto UDP %s -- otro proceso del host lo tiene "
                "tomado. El plugin se queda sin funcionar hasta resolverlo.", LISTEN_PORT,
            )
            raise
        self._sock = sock
        threading.Thread(target=self._recv_loop, name="govee-recv", daemon=True).start()
        threading.Thread(target=self._poll_loop, name="govee-poll", daemon=True).start()

    def _recv_loop(self) -> None:
        while True:
            try:
                raw, addr = self._sock.recvfrom(4096)
            except OSError:
                log.exception("Govee: fallo leyendo del socket UDP -- se detiene el receptor")
                return
            # BUG REAL: todo lo de abajo estaba fuera de cualquier proteccion
            # amplia. `json.loads(b"[1,2]")` devuelve una LISTA, y `.get` sobre
            # una lista lanza AttributeError -- que no es ValueError ni
            # UnicodeDecodeError, asi que escapaba y MATABA el hilo receptor
            # (via threading.excepthook, a stderr, sin pasar por el log). A
            # partir de ese momento TODAS las bombillas Govee quedaban como
            # desconectadas para siempre, sin una sola linea de log. Y con
            # `host_network: true` basta con que cualquier proceso del host o de
            # la LAN mande un JSON cualquiera al UDP 4002 para provocarlo.
            try:
                payload = json.loads(raw.decode("utf-8"))
                msg = payload.get("msg") if isinstance(payload, dict) else None
                if not isinstance(msg, dict):
                    continue
                cmd = msg.get("cmd")
                data = msg.get("data")
                data = data if isinstance(data, dict) else {}
                if cmd == "scan":
                    self._on_scan_response(addr[0], data)
                elif cmd == "devStatus":
                    self._on_status_response(addr[0], data)
            except (ValueError, UnicodeDecodeError):
                continue  # datagrama que no es JSON valido: normal en una LAN, se ignora
            except Exception:
                # Cualquier otro fallo procesando UN datagrama no puede tumbar
                # el receptor entero.
                log.exception("Govee: fallo procesando un datagrama de %s -- se ignora", addr[0])

    def _on_scan_response(self, ip: str, data: dict) -> None:
        device = data.get("device")
        if not device:
            return
        with self._scan_lock:
            # Se alimenta a TODOS los escaneos en curso (antes habia un unico
            # hueco compartido, ver `discover`).
            for results in self._active_scans:
                results[device] = {"ip": ip, "sku": data.get("sku"), "device": device}

    def _on_status_response(self, ip: str, data: dict) -> None:
        with self._lock:
            device_id = self._ip_to_id.get(ip)
            if device_id is None:
                return
            self._devices[device_id]["status"] = data
            self._devices[device_id]["last_seen"] = time.time()
        if self._on_any_change:
            try:
                self._on_any_change(device_id)
            except Exception:
                log.exception("Fallo en on_any_change para %s", device_id)

    def _poll_loop(self) -> None:
        while True:
            time.sleep(POLL_INTERVAL_SECONDS)
            try:
                with self._lock:
                    ips = [d["ip"] for d in self._devices.values()]
                for ip in ips:
                    self._send(ip, "devStatus", {})
            except Exception:
                # Mismo criterio que el receptor: un fallo puntual no puede
                # dejar sin sondeo a todos los dispositivos para siempre.
                log.exception("Govee: fallo en el ciclo de sondeo -- se reintenta en el proximo")

    # --------------------------------------------------------- descubrimiento

    def discover(self, timeout: float = SCAN_WINDOW_SECONDS) -> list[dict]:
        """BUG REAL: antes habia UN solo hueco compartido (`_scan_results`) con
        una espera de varios segundos en medio. Dos escaneos concurrentes (dos
        `POST /api/discover`, y Flask es multihilo) se pisaban: el primero que
        terminaba lo ponia a None y el segundo reventaba con
        `'NoneType' object has no attribute 'values'`, que la ruta convertia en
        un 502. Ahora cada escaneo tiene su PROPIO dict, registrado en una lista
        de escaneos activos que el receptor alimenta a todos por igual -- dos
        escaneos a la vez funcionan y ademas comparten las respuestas."""
        results: dict[str, dict] = {}
        with self._scan_lock:
            self._active_scans.append(results)
        try:
            self._send(MULTICAST_IP, "scan", {"account_topic": "reserve"})
            time.sleep(timeout)
        finally:
            with self._scan_lock:
                self._active_scans.remove(results)
        return list(results.values())

    # --------------------------------------------------------- dispositivos

    def add_device(self, device_id: str, ip: str) -> None:
        with self._lock:
            self._devices[device_id] = {"ip": ip, "status": None, "last_seen": 0.0}
            self._ip_to_id[ip] = device_id
        self._send(ip, "devStatus", {})

    def remove_device(self, device_id: str) -> None:
        with self._lock:
            info = self._devices.pop(device_id, None)
            if info:
                self._ip_to_id.pop(info["ip"], None)

    def connected(self, device_id: str) -> bool:
        with self._lock:
            info = self._devices.get(device_id)
            if not info or info["status"] is None:
                return False
            return (time.time() - info["last_seen"]) < STALE_AFTER_SECONDS

    def get_status(self, device_id: str) -> dict | None:
        with self._lock:
            info = self._devices.get(device_id)
            return dict(info["status"]) if info and info["status"] else None

    def _ip_of(self, device_id: str) -> str | None:
        with self._lock:
            info = self._devices.get(device_id)
            return info["ip"] if info else None

    # ------------------------------------------------------------ escritura

    def _send(self, ip: str, cmd: str, data: dict) -> None:
        if self._sock is None:
            return
        payload = json.dumps({"msg": {"cmd": cmd, "data": data}}).encode("utf-8")
        port = SCAN_PORT if ip == MULTICAST_IP else CONTROL_PORT
        try:
            self._sock.sendto(payload, (ip, port))
        except OSError:
            log.exception("Govee: fallo enviando '%s' a %s", cmd, ip)

    def turn_on(self, device_id: str, brightness_pct: float | None = None,
                color_temp_kelvin: float | None = None, hs: tuple[float, float] | None = None) -> None:
        ip = self._ip_of(device_id)
        if ip is None:
            raise KeyError(f"dispositivo Govee desconocido: {device_id}")
        self._send(ip, "turn", {"value": 1})
        if color_temp_kelvin is not None:
            kelvin = int(_clamp(round(color_temp_kelvin), MIN_KELVIN, MAX_KELVIN))
            self._send(ip, "colorwc", {"color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": kelvin})
        elif hs is not None:
            r, g, b = _hs_to_rgb(hs[0], hs[1])
            self._send(ip, "colorwc", {"color": {"r": r, "g": g, "b": b}, "colorTemInKelvin": 0})
        if brightness_pct is not None:
            self._send(ip, "brightness", {"value": int(_clamp(round(brightness_pct), 1, 100))})
        # Repintado optimista LOCAL (mismo criterio que `manual_command`
        # de Lighting con su luz dummy): el proximo `devStatus` -- pedido
        # aqui mismo debajo, o el del sondeo periodico -- ya confirma el
        # valor real, pero el dashboard no tiene que esperar a eso.
        self._merge_optimistic(device_id, on=True, brightness_pct=brightness_pct, color_temp_kelvin=color_temp_kelvin)
        self._send(ip, "devStatus", {})

    def turn_off(self, device_id: str) -> None:
        ip = self._ip_of(device_id)
        if ip is None:
            raise KeyError(f"dispositivo Govee desconocido: {device_id}")
        self._send(ip, "turn", {"value": 0})
        self._merge_optimistic(device_id, on=False)
        self._send(ip, "devStatus", {})

    def _merge_optimistic(self, device_id: str, on: bool | None = None,
                           brightness_pct: float | None = None, color_temp_kelvin: float | None = None) -> None:
        with self._lock:
            info = self._devices.get(device_id)
            if info is None:
                return
            status = dict(info["status"] or {})
            if on is not None:
                status["onOff"] = 1 if on else 0
            if brightness_pct is not None:
                status["brightness"] = round(brightness_pct)
            if color_temp_kelvin is not None:
                status["colorTemInKelvin"] = round(color_temp_kelvin)
            info["status"] = status

    # ------------------------------------------------------- fachada light

    def light_handle(self, device_id: str) -> "GoveeLightHandle | None":
        with self._lock:
            has_lan = device_id in self._devices
        # Un dispositivo SOLO cloud (nunca visto en LAN, o modelo sin
        # soporte LAN) no tiene entrada en `self._devices` -- sigue
        # siendo un handle valido si tiene identidad de cuenta registrada
        # (ver `set_cloud_identity`), o `connected()`/`_cloud_status()`
        # simplemente no encontrarian nada por ningun camino.
        if not has_lan and device_id not in self._cloud_identity:
            return None
        return GoveeLightHandle(self, device_id)


class GoveeLightHandle:
    """Mismo contrato que `TplinkLightHandle`/`TuyaLightHandle`
    (available/is_on/brightness_pct/color_temp_kelvin/turn_on/turn_off)
    -- Lighting no necesita saber que esto es Govee, ni si la orden fue
    por LAN o por la nube.

    LAN sigue siendo la via PRIMARIA (rapida, sin limite de peticiones):
    cuando `manager.connected()` es True para este dispositivo, todo pasa
    por LAN igual que siempre. Solo cuando LAN no responde (dispositivo
    sin soporte LAN, o "LAN API" sin activar en la app oficial para ese
    modelo) se cae al respaldo cloud, si hay identidad de cuenta e
    api_key configuradas (ver `set_cloud_identity`/`set_cloud_api_key`)."""

    def __init__(self, manager: GoveeDeviceManager, device_id: str) -> None:
        self._manager = manager
        self._device_id = device_id

    def _status(self) -> dict | None:
        if self._manager.connected(self._device_id):
            return self._manager.get_status(self._device_id)
        return self._manager._cloud_status(self._device_id)

    @property
    def available(self) -> bool:
        if self._manager.connected(self._device_id):
            return True
        return self._manager._cloud_status(self._device_id) is not None

    @property
    def is_on(self) -> bool:
        status = self._status()
        return bool(status and status.get("onOff") == 1)

    @property
    def brightness_pct(self) -> float | None:
        status = self._status()
        if not status or status.get("brightness") is None:
            return None
        return float(status["brightness"])

    @property
    def color_temp_kelvin(self) -> int | None:
        status = self._status()
        if not status:
            return None
        kelvin = status.get("colorTemInKelvin")
        # Govee reporta 0 cuando el modo activo es color RGB, no
        # temperatura de color -- 0 no es un Kelvin valido, es "no aplica
        # ahora mismo" (mismo criterio que `_color_temp_active` de TP-Link).
        return int(kelvin) if kelvin else None

    def turn_on(self, brightness_pct: float | None = None, color_temp_kelvin: float | None = None,
                hs: tuple[float, float] | None = None) -> None:
        if self._manager.connected(self._device_id):
            self._manager.turn_on(self._device_id, brightness_pct=brightness_pct, color_temp_kelvin=color_temp_kelvin, hs=hs)
            return
        # El respaldo cloud no soporta color HS -- solo temperatura de
        # color y brillo (ver documentacion oficial); si el llamante solo
        # pidio HS y no hay LAN, se enciende igualmente sin ese matiz en
        # vez de no hacer nada.
        self._manager._cloud_control(self._device_id, on=True, brightness_pct=brightness_pct, color_temp_kelvin=color_temp_kelvin)

    def turn_off(self) -> None:
        if self._manager.connected(self._device_id):
            self._manager.turn_off(self._device_id)
            return
        self._manager._cloud_control(self._device_id, on=False)
