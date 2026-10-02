"""Lista de habitaciones del aspirador desde el mapa guardado en la NUBE (no del
robot). Flujo VERIFICADO en vivo (Conga X80):
  1. thing.m.dev.storage.config.get (type=Common, devId) -> credenciales S3.
  2. thing.m.dev.common.file.list (fileType=pic, limit, offset) -> ficheros
     reales `<common>/history/time_<t>_mapid_<N>.bin` (el mas reciente primero).
  3. GET presignado (cloud_storage.fetch) del mas reciente -> JSON del mapa.
  4. mapAdditional.roomProperty.ids / names -> {id: nombre}.
El layout estatico `<common>/layout/lay.bin` puede no existir; los ficheros de
history SIEMPRE tienen el mapa con las habitaciones (ids+names).
"""
from __future__ import annotations
import gzip, json, logging
from . import cloud_storage

log = logging.getLogger("tuya_native")


def _storage_config(api, device_id: str) -> cloud_storage.CloudStorageConfig:
    d = api.call("thing.m.dev.storage.config.get", "1.0",
                 post_data={"type": "Common", "devId": device_id}, session_require=True)
    return cloud_storage.CloudStorageConfig.from_json(d)


def latest_map_file(api, device_id: str) -> str | None:
    """Ruta del mapa mas reciente en la nube, o None. Barato (una llamada): sirve
    para saber si hay un mapa NUEVO sin descargarlo."""
    try:
        return _latest_map_file(api, device_id)
    except Exception:
        log.debug("tuya_native: no se pudo listar los mapas de %s", device_id, exc_info=True)
        return None


def _latest_map_file(api, device_id: str) -> str | None:
    r = api.call("thing.m.dev.common.file.list", "1.0",
                 post_data={"devId": device_id, "fileType": "pic", "limit": 5, "offset": 0},
                 session_require=True)
    datas = (r or {}).get("datas") or []
    # el mas reciente primero (la API ya los da ordenados por time desc)
    for d in datas:
        f = d.get("file")
        if f and f.endswith(".bin"):
            return f
    return None


def fetch_rooms(api, device_id: str, path: str | None = None) -> dict:
    """{segment_id(str): nombre}. {} si no hay mapa/habitaciones o si algo falla
    (nunca lanza: la limpieza normal del aspirador no debe depender de esto).
    `path`: fichero concreto (el de `latest_map_file`); si no, el mas reciente."""
    try:
        cfg = _storage_config(api, device_id)
        path = path or _latest_map_file(api, device_id)
        if not path:
            return {}
        data = cloud_storage.fetch(cfg, path)
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        doc = json.loads(data)
        rp = (doc.get("mapAdditional") or {}).get("roomProperty") or {}
        ids = rp.get("ids") or []
        names = rp.get("names") or []
        rooms = {}
        for i, rid in enumerate(ids):
            name = names[i].strip() if i < len(names) and names[i] else str(rid)
            rooms[str(rid)] = name
        return rooms
    except Exception:
        log.exception("tuya_native: fallo obteniendo habitaciones del mapa de %s", device_id)
        return {}
