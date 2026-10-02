"""
Detecta cuando el consumo real medido ahora mismo se dispara muy por
encima de lo que la previsión (media historica de esa hora del dia)
esperaba — señal de que algo se ha quedado encendido o hay un consumo
fuera de lo normal.

Nada de aprendizaje automatico: se compara el dato de ahora contra la
previsión ya existente, con un margen relativo Y un minimo absoluto (para
no disparar con bases de consumo pequeñas donde un 60% de mas son 30W sin
importancia), y se exige que se mantenga unos minutos seguidos antes de
avisar — para no reaccionar a un pico de un instante — y otros tantos
de consumo normal antes de desactivar la alerta.
"""

from __future__ import annotations

import os
import threading

import json_store
from datetime import datetime

ANOMALY_PATH = os.environ.get("ANOMALY_PATH", "/data/anomaly.json")

THRESHOLD_RATIO = 1.6   # el consumo real tiene que superar la previsión en un 60%...
THRESHOLD_MIN_W = 400   # ...Y superarla en al menos 400W, para no disparar con bases pequeñas
# Cuanto tiempo SEGUIDO tiene que mantenerse por encima (o por debajo) antes de
# cambiar de estado.
#
# BUG REAL, visto en el log de produccion (anomalia "detectada" a las 07:44:25 y
# "resuelta" a las 07:44:31, con su notificacion de HA creada y borrada): esto
# se contaba en CICLOS (3 seguidos), pensado para un ciclo por minuto. Con el
# ciclo reactivo hay ~2 ciclos por segundo, asi que "3 ciclos seguidos" eran
# segundo y medio: cualquier pico de un instante (un hervidor, el arranque de un
# compresor) disparaba el aviso. Se cuenta en tiempo real, que no depende de
# cada cuanto se ejecute el ciclo.
CONFIRM_SECONDS = 180
CLEAR_SECONDS = 180

_lock = threading.RLock()


def _default() -> dict:
    return {
        "status": "ok", "since": None, "over_since": None, "under_since": None,
        "live_load_w": None, "expected_load_w": None,
    }


def _seconds_since(iso: str | None, now: datetime) -> float:
    if not iso:
        return 0.0
    try:
        return max(0.0, (now - datetime.fromisoformat(iso)).total_seconds())
    except (TypeError, ValueError):
        return 0.0


def _load() -> dict:
    data = json_store.load(ANOMALY_PATH)
    if not isinstance(data, dict):
        return _default()
    merged = _default()
    merged.update(data)
    return merged


def _save(data: dict) -> None:
    # Copia en memoria + volcado a disco diferido y atomico: ver json_store.py
    # (este fichero se reescribia entero en cada ciclo de planificacion).
    json_store.save(ANOMALY_PATH, data)


def update(now: datetime, live_load_w: float, expected_load_w: float) -> dict:
    """
    Llamar una vez por ciclo con el consumo real medido ahora mismo y el
    esperado para esta hora. Devuelve el estado tras aplicar esta lectura,
    con "changed": True si el estado (ok/anomaly) acaba de cambiar en este
    ciclo — para saber cuando toca notificar en vez de repetir cada minuto.
    """
    # Ciclo completo lectura-modificacion-escritura bajo el mismo lock que
    # ya protege `_load`/`_save` por separado -- ver el mismo arreglo en
    # lifetime_store.accumulate.
    with _lock:
        data = _load()
        was_status = data["status"]

        is_over = (
            live_load_w > expected_load_w * THRESHOLD_RATIO
            and live_load_w - expected_load_w > THRESHOLD_MIN_W
        )

        if is_over:
            data["under_since"] = None
            if not data.get("over_since"):
                data["over_since"] = now.isoformat()
        else:
            data["over_since"] = None
            if not data.get("under_since"):
                data["under_since"] = now.isoformat()

        if data["status"] == "ok" and is_over and _seconds_since(data["over_since"], now) >= CONFIRM_SECONDS:
            data["status"] = "anomaly"
            data["since"] = now.isoformat()
        elif data["status"] == "anomaly" and not is_over and _seconds_since(data["under_since"], now) >= CLEAR_SECONDS:
            data["status"] = "ok"
            data["since"] = None
        # restos del conteo por ciclos de versiones anteriores
        data.pop("over_streak", None)
        data.pop("under_streak", None)

        data["live_load_w"] = round(live_load_w)
        data["expected_load_w"] = round(expected_load_w)
        _save(data)

        return {**{k: v for k, v in data.items() if k not in ("over_since", "under_since")},
                "changed": data["status"] != was_status}


def get_status() -> dict:
    data = _load()
    return {k: v for k, v in data.items()
            if k not in ("over_streak", "under_streak", "over_since", "under_since")}
