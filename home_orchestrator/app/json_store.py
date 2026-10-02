"""
Lectura/escritura de los ficheros JSON de estado de Energy (`/data/*.json`)
con copia en memoria y volcado a disco DIFERIDO.

EL PROBLEMA, medido en produccion
---------------------------------
Cada almacen (`history_store`, `savings_store`, `grid_energy_store`,
`lifetime_store`, `capacity_store`, `monotonic_sensor`, `deferrable_store`,
`anomaly_store`...) leia su fichero entero del disco y lo reescribia entero en
CADA llamada. Y `run_cycle` los llama a todos en cada ciclo. Con el ciclo
reactivo eso son ~115 ciclos por minuto (medido en el log real), es decir del
orden de veinte reescrituras atomicas por segundo -- una de ellas, el historico,
de 50 KB -- sobre el almacenamiento del host de Home Assistant, las 24 horas.

LA SOLUCION
-----------
Una copia en memoria por fichero. Las lecturas salen de ahi; las escrituras
actualizan la copia y solo se vuelcan a disco como mucho una vez cada
`FLUSH_INTERVAL_SECONDS` (un hilo de fondo recoge lo pendiente). El contenido y
el formato de los ficheros son exactamente los de antes -- nada que migrar.

Lo que se pierde ante un corte brusco del proceso: como mucho los ultimos
`FLUSH_INTERVAL_SECONDS` de acumulado. Los contadores de red salen de los
incrementos del contador del medidor (se recuperan solos al volver) y el resto
son integraciones de potencia de unos segundos.

Un fichero modificado DESDE FUERA (restaurar una copia de seguridad, ver
core_backup.py) se detecta por su fecha de modificacion y se relee: lo de
disco manda sobre la copia en memoria.
"""

from __future__ import annotations

import atexit
import copy
import json
import logging
import os
import threading
import time

log = logging.getLogger("json_store")

FLUSH_INTERVAL_SECONDS = 10.0
_FLUSHER_PERIOD_SECONDS = 2.0

_lock = threading.RLock()
# path -> {"data": obj | None, "dirty": bool, "last_write": monotonic | None, "mtime": st_mtime_ns | None}
_entries: dict[str, dict] = {}
_flusher_started = False


def _disk_mtime(path: str) -> int | None:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def _read_disk(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _write_disk(path: str, data) -> None:
    """Escritura ATOMICA (.tmp + os.replace): un corte a mitad de un
    `open(..., "w")` directo dejaba el fichero truncado o con dos objetos JSON
    concatenados -- ver config_store._write_raw."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _flush_entry(path: str, entry: dict) -> None:
    """Llamar con `_lock` cogido."""
    if not entry["dirty"]:
        return
    try:
        _write_disk(path, entry["data"])
    except OSError:
        log.warning("No se pudo escribir %s (se reintenta en el siguiente volcado)", path, exc_info=True)
        return
    entry["dirty"] = False
    entry["last_write"] = time.monotonic()
    entry["mtime"] = _disk_mtime(path)


def _entry(path: str) -> dict:
    """Entrada de cache al dia. Llamar con `_lock` cogido."""
    entry = _entries.get(path)
    mtime = _disk_mtime(path)
    if entry is None:
        entry = {"data": _read_disk(path) if mtime is not None else None,
                 "dirty": False, "last_write": None, "mtime": mtime}
        _entries[path] = entry
    elif mtime != entry["mtime"]:
        # El fichero ha cambiado (o desaparecido) por fuera desde nuestra
        # ultima lectura/escritura: lo de disco manda, incluso sobre cambios
        # en memoria aun sin volcar.
        entry["data"] = _read_disk(path) if mtime is not None else None
        entry["dirty"] = False
        entry["mtime"] = mtime
    return entry


def load(path: str):
    """Contenido del fichero (una COPIA, se puede modificar libremente), o
    `None` si no existe o no es JSON valido -- quien llama pone su valor por
    defecto, igual que hacia antes con su propio `try/except`."""
    with _lock:
        data = _entry(path)["data"]
        return copy.deepcopy(data) if data is not None else None


def save(path: str, data) -> None:
    """Guarda `data` como contenido del fichero. Disponible al instante para
    `load`; a disco va ya mismo si el ultimo volcado es antiguo, o lo recoge
    el hilo de fondo."""
    with _lock:
        entry = _entry(path)
        entry["data"] = copy.deepcopy(data)
        entry["dirty"] = True
        # `last_write` None = todavia no se ha volcado nunca en este proceso:
        # la primera escritura va a disco ya (el origen de `time.monotonic()`
        # no esta definido, no se puede comparar contra 0).
        if entry["last_write"] is None or time.monotonic() - entry["last_write"] >= FLUSH_INTERVAL_SECONDS:
            _flush_entry(path, entry)
    _ensure_flusher()


def flush_all() -> None:
    """Vuelca a disco todo lo pendiente (parada ordenada, pruebas)."""
    with _lock:
        for path, entry in _entries.items():
            _flush_entry(path, entry)


def _flusher_loop() -> None:
    while True:
        time.sleep(_FLUSHER_PERIOD_SECONDS)
        try:
            with _lock:
                now = time.monotonic()
                for path, entry in _entries.items():
                    if entry["dirty"] and (entry["last_write"] is None
                                           or now - entry["last_write"] >= FLUSH_INTERVAL_SECONDS):
                        _flush_entry(path, entry)
        except Exception:
            log.exception("Fallo en el volcado periodico de los ficheros de estado")


def _ensure_flusher() -> None:
    global _flusher_started
    if _flusher_started:
        return
    with _lock:
        if _flusher_started:
            return
        _flusher_started = True
        threading.Thread(target=_flusher_loop, name="json-store-flusher", daemon=True).start()
        atexit.register(flush_all)
