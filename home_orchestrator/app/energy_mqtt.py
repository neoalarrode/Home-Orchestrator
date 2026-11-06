"""
Sensores de Energy publicados por MQTT Discovery en vez de por la API REST de
HA, para los que el usuario quiere poder asignar a una zona (5-nov: EX01, el
enchufe del servidor). Un sensor creado con POST /api/states no tiene
unique_id: HA no lo mete en el registro de entidades y no deja asignarle zona
ni cambiarle nada desde la interfaz.

Se conserva el MISMO entity_id que tenia por REST (`default_entity_id`), para
no romper el Panel de Energia ni partir el historico. Antes de anunciarlo se
borra el estado REST (si no, HA veria el id ocupado y le pondria "_2").
Sin broker MQTT disponible, `publish` devuelve False y quien llama sigue por REST.
"""

from __future__ import annotations

import json
import logging
import threading

log = logging.getLogger(__name__)

DISCOVERY_PREFIX = "homeassistant"
NODE_ID = "home_orchestrator_energy"

_lock = threading.Lock()
_client = None
_connect_tried = False
_announced: set[str] = set()


def _get_client():
    global _client, _connect_tried
    with _lock:
        if _client is None and not _connect_tried:
            _connect_tried = True
            try:
                import ha_mqtt
                c = ha_mqtt.HAMqttClient(client_id=NODE_ID)
                if c.connect():
                    _client = c
            except Exception:
                log.warning("Energy: sin MQTT, los sensores siguen por la API REST", exc_info=True)
        return _client


def _object_id(entity_id: str) -> str:
    return entity_id.split(".", 1)[1]


def discovery_payload(entity_id: str, attributes: dict) -> dict:
    obj = _object_id(entity_id)
    base = f"{DISCOVERY_PREFIX}/sensor/{NODE_ID}/{obj}"
    payload = {
        "name": attributes.get("friendly_name") or obj,
        "unique_id": f"{NODE_ID}_{obj}",
        "default_entity_id": entity_id,
        "state_topic": f"{base}/state",
        "json_attributes_topic": f"{base}/attributes",
    }
    for key in ("device_class", "state_class", "unit_of_measurement"):
        if attributes.get(key):
            payload[key] = attributes[key]
    return payload


def publish(entity_id: str, state, attributes: dict) -> bool:
    """Publica `entity_id` por MQTT (anunciandolo la primera vez). False si
    no hay MQTT: entonces hay que publicarlo por REST."""
    client = _get_client()
    if client is None or not client.connected:
        return False
    obj = _object_id(entity_id)
    base = f"{DISCOVERY_PREFIX}/sensor/{NODE_ID}/{obj}"
    if entity_id not in _announced:
        try:
            import ha_client
            ha_client.delete_state(entity_id)
        except Exception:
            log.debug("No se pudo borrar el estado REST de %s", entity_id, exc_info=True)
        client.publish(f"{base}/config", discovery_payload(entity_id, attributes), retain=True)
        _announced.add(entity_id)
    extra = {k: v for k, v in attributes.items()
             if k not in ("friendly_name", "device_class", "state_class", "unit_of_measurement")}
    client.publish(f"{base}/attributes", json.dumps(extra, default=str), retain=True)
    client.publish(f"{base}/state", state, retain=True)
    return True
