"""
Historico ligero de decisiones ya ejecutadas (no previstas), para poder
mostrar la tabla completa del dia (00:00 a 00:00) mezclando lo que ya paso
con lo que queda por delante.

Una entrada por HORA de reloj (clave "YYYY-MM-DDTHH"): cada ciclo dentro de
esa hora sobreescribe la entrada con la ultima decision real tomada, asi
que al cerrar la hora queda registrado lo que de verdad se aplico.
"""

from __future__ import annotations

import os
import threading

import json_store
from datetime import datetime, timedelta

HISTORY_PATH = os.environ.get("HISTORY_PATH", "/data/history.json")
MAX_AGE_HOURS = 24 * 8  # 8 dias: cubre la comparativa de "hoy vs media de los ultimos 7 dias"
# Hueco maximo entre dos ciclos que se integra en la media de la hora.
MAX_AVERAGE_GAP_SECONDS = 300

_lock = threading.RLock()


def _hour_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H")


def _load() -> dict:
    data = json_store.load(HISTORY_PATH)
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> None:
    # Copia en memoria + volcado a disco diferido y atomico: ver json_store.py
    # (este fichero se reescribia entero en cada ciclo de planificacion).
    json_store.save(HISTORY_PATH, data)


# Campos internos de una entrada (media en curso de la hora): no son datos.
_ACC_KEY = "_avg"


def _public(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k != _ACC_KEY}


def record(now: datetime, entry: dict, averaged_fields: tuple[str, ...] = ()) -> None:
    """Guarda/actualiza la entrada de la hora actual con la decision real tomada.

    `averaged_fields`: campos de POTENCIA (W) que se guardan como media
    ponderada por tiempo de toda la hora en vez de como el ultimo valor.

    BUG REAL: cada ciclo SOBREESCRIBIA la entrada entera, asi que al cerrar la
    hora quedaba la ultima lectura instantanea. Con la prevision (una media
    horaria) daba igual, pero desde que `pv_w`/`load_w` son lecturas EN VIVO,
    "la hora" era lo que marcasen los sensores en su ultimo segundo: un horno
    encendido a las 13:59 convertia las 13:00 en una hora de 3 kW. Y esos
    numeros se suman como Wh (1 entrada = 1 hora) en la comparativa de consumo
    y en la reconstruccion del historial de energia.
    """
    # Ciclo completo lectura-modificacion-escritura bajo el mismo lock --
    # ver el mismo arreglo en lifetime_store.accumulate.
    with _lock:
        data = _load()
        key = _hour_key(now)
        new_entry = dict(entry)

        if averaged_fields:
            prev = data.get(key) or {}
            acc = prev.get(_ACC_KEY) or {}
            sums = dict(acc.get("sums") or {})
            seconds = float(acc.get("seconds") or 0.0)
            last_vals = acc.get("last") or {}
            try:
                dt_s = (now - datetime.fromisoformat(acc["ts"])).total_seconds() if acc.get("ts") else 0.0
            except (TypeError, ValueError):
                dt_s = 0.0
            # Cada lectura vale hasta la siguiente. Un hueco largo (addon
            # parado) no se rellena con la lectura anterior.
            if 0.0 < dt_s <= MAX_AVERAGE_GAP_SECONDS:
                for field in averaged_fields:
                    if last_vals.get(field) is not None:
                        sums[field] = float(sums.get(field, 0.0)) + float(last_vals[field]) * dt_s
                seconds += dt_s
            if seconds > 0:
                for field in averaged_fields:
                    if field in sums:
                        new_entry[field] = round(sums[field] / seconds)
            new_entry[_ACC_KEY] = {
                "ts": now.isoformat(), "seconds": seconds, "sums": sums,
                "last": {f: entry.get(f) for f in averaged_fields},
            }

        data[key] = new_entry

        cutoff = now - timedelta(hours=MAX_AGE_HOURS)
        cutoff_key = _hour_key(cutoff)
        data = {k: v for k, v in data.items() if k >= cutoff_key}
        # la media en curso solo hace falta en la hora abierta
        for k, v in data.items():
            if k != key and isinstance(v, dict) and _ACC_KEY in v:
                v.pop(_ACC_KEY, None)

        _save(data)


def get_all() -> list[dict]:
    """Todo lo que quede retenido (hasta MAX_AGE_HOURS, 8 dias), ordenado
    por hora — para reconstruir un historico real en vez de un salto de
    golpe (ver ha_statistics.py)."""
    data = _load()
    return [_public(v) for k, v in sorted(data.items())]


def get_today(now: datetime) -> list[dict]:
    """Entradas ya ejecutadas de HOY (desde las 00:00 hasta la hora actual, sin incluirla)."""
    data = _load()
    today_prefix = now.strftime("%Y-%m-%d")
    entries = [_public(v) for k, v in sorted(data.items()) if k.startswith(today_prefix) and k < _hour_key(now)]
    return entries


def get_recent_days_consumption(now: datetime, days: int = 7) -> dict | None:
    """
    Compara el consumo acumulado de HOY (desde las 00:00 hasta ahora) con la
    media de los `days` dias anteriores, cada uno hasta la MISMA hora del
    dia — para que la comparacion sea justa (medio dia contra medio dia, no
    contra un dia entero). Se calcula solo a partir del propio historico ya
    guardado (campo "load_w" de cada hora), nada nuevo que pedir a HA.

    Devuelve None si no hay al menos un dia previo completo con el que
    comparar (instalacion recien estrenada).
    """
    data = _load()
    today_prefix = now.strftime("%Y-%m-%d")
    current_hour = now.hour

    by_date: dict[str, float] = {}
    for k, v in data.items():
        date_part, hour_part = k.split("T")
        if int(hour_part) >= current_hour:
            continue
        load_w = v.get("load_w")
        if load_w is None:
            continue
        by_date[date_part] = by_date.get(date_part, 0.0) + load_w / 1000.0  # Wh -> kWh (1 entrada = 1 hora)

    today_kwh = by_date.pop(today_prefix, 0.0)
    past_dates = sorted(by_date.keys())[-days:]
    if not past_dates:
        return None

    avg_kwh = sum(by_date[d] for d in past_dates) / len(past_dates)
    if avg_kwh <= 0:
        return None

    return {
        "today_kwh": round(today_kwh, 2),
        "avg_kwh": round(avg_kwh, 2),
        "days_compared": len(past_dates),
        "delta_pct": round(100 * (today_kwh - avg_kwh) / avg_kwh, 1),
    }
