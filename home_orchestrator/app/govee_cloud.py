"""
Cliente para la API REST oficial de Govee (developer-api.govee.com) --
via CLOUD, a peticion expresa del usuario: no todos los dispositivos
Govee funcionan por LAN (algunos modelos no la soportan en absoluto, o
el usuario no la ha activado a mano en la app oficial para ese
dispositivo en concreto), y sin esto esos equipos simplemente no
respondian nunca (ver docstring de `govee/device_manager.py`).

A diferencia del protocolo LAN (reimplementado en crudo, sin
documentacion oficial), esta es la API REST PUBLICA Y DOCUMENTADA del
fabricante (https://developer.govee.com/reference/get-you-devices) --
solo necesita una API key (se pide gratis desde la app oficial: Perfil ->
Configuracion -> Acerca de nosotros -- no hace falta email/contraseña de
la cuenta, a diferencia del canal AWS IoT no documentado que usa
govee2mqtt y que aqui NO se implementa, mismo criterio de "sin cajas
negras" aplicado al resto de Home Orchestrator con lo que SI hay
alternativa documentada).

Limite de la API (documentado por el fabricante): 10.000 peticiones/dia
por cuenta y un maximo de 1 peticion cada pocos segundos por dispositivo
para comandos de control -- de ahi `MIN_SECONDS_BETWEEN_CONTROL` mas
abajo. Pensada como VIA DE RESPALDO (LAN sigue siendo la primaria donde
funcione, ver `device_manager.py`), nunca para sondeo continuo.
"""

from __future__ import annotations

import logging
import threading
import time

import requests

log = logging.getLogger("govee_cloud")

BASE_URL = "https://developer-api.govee.com/v1"
REQUEST_TIMEOUT_SECONDS = 10

# Nunca mandar dos comandos de control seguidos al MISMO dispositivo mas
# rapido que esto -- documentado por el fabricante como limite de facto,
# y de todas formas la via LAN ya cubre el "tiempo real" cuando esta
# disponible; el cloud es solo el respaldo para lo que LAN no alcanza.
MIN_SECONDS_BETWEEN_CONTROL = 3.0

_last_control_at: dict[str, float] = {}
_last_control_lock = threading.Lock()


def _headers(api_key: str) -> dict:
    return {"Govee-API-Key": api_key, "Content-Type": "application/json"}


def list_devices(api_key: str) -> list[dict] | None:
    """Todos los dispositivos de la cuenta segun la nube -- incluye los
    que NUNCA responden por LAN (modelo sin soporte, o LAN API no
    activada). `None` si la API no responde o la key no es valida --
    nunca una lista vacia inventada."""
    try:
        r = requests.get(f"{BASE_URL}/devices", headers=_headers(api_key), timeout=REQUEST_TIMEOUT_SECONDS)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException:
        log.exception("Govee Cloud: fallo listando dispositivos")
        return None
    devices = (data.get("data") or {}).get("devices")
    return devices if isinstance(devices, list) else None


def get_state(api_key: str, device_mac: str, sku: str) -> dict | None:
    """Estado actual (encendido, brillo, color/temperatura) de un
    dispositivo por su identificador de cuenta (MAC + modelo) -- NUNCA
    por IP, la nube no sabe de direcciones LAN. `None` si no responde."""
    try:
        r = requests.get(
            f"{BASE_URL}/devices/state",
            headers=_headers(api_key),
            params={"device": device_mac, "model": sku},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException:
        log.exception("Govee Cloud: fallo leyendo estado de %s", device_mac)
        return None
    properties = (data.get("data") or {}).get("properties")
    if not isinstance(properties, list):
        return None
    # La API devuelve una LISTA de objetos de un campo cada uno
    # ({"online": true}, {"powerState": "on"}, ...) -- se aplana a un
    # unico dict, mas comodo para el resto del modulo.
    merged: dict = {}
    for prop in properties:
        if isinstance(prop, dict):
            merged.update(prop)
    return merged


def _control_allowed(device_mac: str, cmd_name: str) -> bool:
    with _last_control_lock:
        last = _last_control_at.get((device_mac, cmd_name))
        return last is None or (time.time() - last) >= MIN_SECONDS_BETWEEN_CONTROL


def _note_control(device_mac: str, cmd_name: str) -> None:
    with _last_control_lock:
        _last_control_at[(device_mac, cmd_name)] = time.time()


def control(api_key: str, device_mac: str, sku: str, cmd_name: str, cmd_value) -> bool:
    """Manda UN comando de control (`turn`/`brightness`/`color`/
    `colorTem`, ver la documentacion oficial para el resto de nombres) --
    `False` sin mandar nada si se ha llamado hace menos de
    `MIN_SECONDS_BETWEEN_CONTROL` para ese MISMO comando en ese mismo
    dispositivo, para respetar el limite de facto del fabricante.
    BUG REAL, confirmado en produccion: la API oficial de Govee NO admite
    mandar varias propiedades en una sola peticion (`cmd` como lista ->
    400 "Invalid cmd", probado contra la cuenta real) -- encender con
    brillo y temperatura de color a la vez son SIEMPRE 3 peticiones
    separadas (`turn`, `brightness`, `colorTem`). El limite antes era por
    DISPOSITIVO a secas, asi que esas 3 peticiones de una misma operacion
    se bloqueaban entre si -- la primera (`turn`) pasaba y las otras dos
    se descartaban en silencio por "acabo de mandar algo a este
    dispositivo". Por comando en vez de por dispositivo: sigue evitando
    repetir la MISMA propiedad demasiado seguido, sin impedir mandar
    varias propiedades distintas en la misma operacion."""
    if not _control_allowed(device_mac, cmd_name):
        log.warning(
            "Govee Cloud: comando '%s' a %s omitido -- se mando el mismo comando hace menos de %ss",
            cmd_name, device_mac, MIN_SECONDS_BETWEEN_CONTROL,
        )
        return False
    payload = {"device": device_mac, "model": sku, "cmd": {"name": cmd_name, "value": cmd_value}}
    try:
        r = requests.put(f"{BASE_URL}/devices/control", headers=_headers(api_key), json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException:
        log.exception("Govee Cloud: fallo mandando '%s' a %s", cmd_name, device_mac)
        return False
    finally:
        _note_control(device_mac, cmd_name)
    ok = data.get("code") == 200
    if ok:
        log.info("Govee Cloud: '%s'=%s mandado a %s", cmd_name, cmd_value, device_mac)
    else:
        log.warning("Govee Cloud: '%s' a %s respondio codigo %s (%s)", cmd_name, device_mac, data.get("code"), data.get("message"))
    return ok
