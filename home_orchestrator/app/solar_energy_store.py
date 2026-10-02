"""
Contador de por vida (sin caducidad) de energia solar total producida
por la casa (arrays con sensor de Home Assistant + puertos MPPT de
baterias EcoFlow, todo junto) -- a diferencia de lifetime_store.py (que
lleva la cuenta POR BATERIA, cargada/descargada), aqui hay una sola
magnitud: toda la solar generada, sin distinguir de que array vino.

Se integra en `_live_sensor_loop` (main.py) a partir de la potencia
solar en vivo (`_live_solar_now_w`), multiplicando por el tiempo real
transcurrido desde la ultima lectura -- no un intervalo fijo asumido,
para no arrastrar error si algun ciclo se salta o tarda mas de la
cuenta. Sirve para sensor.battery_orchestrator_solar_energy (kWh,
state_class total_increasing), el sensor que pide el Panel de Energia
oficial de HA para "Produccion de energia solar" (distinto del sensor
de potencia en W, que solo vale para "Energia de produccion solar" en
tiempo real, no para el acumulado).
"""

from __future__ import annotations

import os
import threading

import json_store
from datetime import datetime

SOLAR_ENERGY_PATH = os.environ.get("SOLAR_ENERGY_PATH", "/data/solar_energy.json")

_lock = threading.RLock()


def _load() -> dict:
    data = json_store.load(SOLAR_ENERGY_PATH)
    if not isinstance(data, dict):
        return {"since": None, "wh": 0.0}
    data.setdefault("since", None)
    data.setdefault("wh", 0.0)
    return data


def _save(data: dict) -> None:
    # Copia en memoria + volcado a disco diferido y atomico: ver json_store.py
    # (este fichero se reescribia entero en cada ciclo de planificacion).
    json_store.save(SOLAR_ENERGY_PATH, data)


def set_total_wh(wh: float, since: str | None = None) -> dict:
    """Fija el acumulado a un valor concreto -- para dejarlo alineado con un
    historico recien reconstruido (ver `/api/energy/backfill_history`).

    Sin esto, reconstruir las estadisticas en HA y dejar el contador local con
    su valor viejo deja los dos numeros peleados: el sensor seguiria contando
    desde el total inflado y la grafica daria un salto en la siguiente
    publicacion."""
    with _lock:
        data = _load()
        data["wh"] = max(0.0, float(wh))
        if since is not None:
            data["since"] = since
        _save(data)
        return data


def accumulate(wh: float) -> None:
    """Suma `wh` (siempre >= 0, energia real movida desde la ultima
    lectura) al total de por vida."""
    if wh <= 0:
        return
    # Ciclo completo bajo el mismo lock: sin el, la reconstruccion del historico
    # (`set_total_wh`) y el bucle en vivo podian pisarse el uno al otro.
    with _lock:
        data = _load()
        if data["since"] is None:
            data["since"] = datetime.now().isoformat()
        data["wh"] += wh
        _save(data)


def get_total_wh() -> dict:
    """{"wh": ..., "since": ...} -- `since` es `None` si todavia no se
    ha registrado ninguna energia."""
    return _load()
