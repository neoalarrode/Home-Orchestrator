"""
Blindaje para los contadores `total_increasing` que publicamos a HA.

EL PROBLEMA, medido en produccion
---------------------------------
`total_increasing` es un contrato: HA da por hecho que ese numero NUNCA baja.
Si baja, lo interpreta como que el contador se ha reiniciado, y cuenta el valor
NUEVO ENTERO como consumo de esa hora.

Nuestros acumulados internos SI pueden bajar: la reconstruccion del historico
(`/api/energy/backfill_history`) recalcula los totales y los deja mas bajos con
`set_totals`, que es justo lo que debe hacer. Pero publicar esa bajada tal cual
destroza el Panel de Energia. Medido contra la instalacion del usuario:

    dia 25  17:03  7913.192 -> 181.009    +181 kWh fantasma
    dia 26  08:42   200.955 -> 188.949    +189 kWh fantasma
    dia 28  18:01   217.808 -> 205.925    +206 kWh fantasma  (el backfill)

El dia 27 no tuvo ninguna bajada, y es el unico que cuadraba con el medidor
real: 13.5 kWh nuestros contra 14.5 del Shelly. Los demas se iban a x10-x15.

LA SOLUCION
-----------
Lo que se publica deja de ser el total interno y pasa a ser un contador propio
que solo acumula los INCREMENTOS positivos de ese total:

    publicado += max(0, total_ahora - total_anterior)

Asi el numero publicado nunca baja (HA nunca ve un reinicio) y, a la vez, no se
pierde consumo futuro: tras una correccion a la baja el contador simplemente
sigue subiendo desde donde estaba. Corregir el pasado es cosa de las
estadisticas (`ha_statistics.import_statistics`, que el backfill ya hace), no
del contador en vivo.
"""

from __future__ import annotations

import json
import logging
import os
import threading

log = logging.getLogger("monotonic_sensor")

STORE_PATH = os.environ.get("MONOTONIC_SENSOR_PATH", "/data/monotonic_sensors.json")

# BUG REAL, confirmado en produccion: un reinicio del addon podia hacer que
# `total` (el acumulado interno que llega aqui) diera un salto de mas de
# 100 kWh de golpe entre dos llamadas -- causa exacta sin confirmar del
# todo (sospecha: identidad de bateria EcoFlow resuelta de forma distinta
# justo tras reconectar), pero el sintoma es siempre el mismo: un `delta`
# fisicamente imposible para el hueco real entre dos ciclos (`run_cycle`
# llama a `publishable()` cada CYCLE_SECONDS, no cada `min_interval` de
# publicacion -- ver `_publish_sensor_throttled`). Con la instalacion mas
# exigente de este proyecto (~5 kW contratados, ~4.8 kW combinados de
# bateria), ni un apagon de varias horas justifica mas de esto entre dos
# lecturas. Un delta que lo supera se trata igual que uno negativo: no se
# publica, solo se actualiza `last_total` para que el SIGUIENTE ciclo
# calcule bien desde ahi -- la alternativa (publicarlo) es exactamente el
# mismo "+206 kWh fantasma" que este modulo ya existe para evitar, solo
# que en sentido positivo en vez de por una bajada.
MAX_PLAUSIBLE_DELTA_KWH = 15.0

_lock = threading.RLock()


def _load() -> dict:
    if not os.path.exists(STORE_PATH):
        return {}
    try:
        with open(STORE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, STORE_PATH)


def publishable(entity_id: str, total: float) -> float:
    """Valor que se puede publicar como `total_increasing` sin mentirle a HA.

    `total` es el acumulado interno, que puede corregirse a la baja. Lo que
    sale de aqui solo sube.
    """
    if total is None:
        return total
    with _lock:
        data = _load()
        estado = data.get(entity_id) or {}
        anterior_total = estado.get("last_total")
        publicado = float(estado.get("published") or 0.0)

        if anterior_total is None:
            # Primera vez: se arranca desde el total actual, sin inventar
            # historia previa.
            publicado = float(total)
        else:
            delta = float(total) - float(anterior_total)
            if delta > MAX_PLAUSIBLE_DELTA_KWH:
                log.warning(
                    "%s: el acumulado interno ha subido de %.3f a %.3f (+%.3f) entre dos "
                    "ciclos -- fisicamente imposible para el hueco real, se descarta como "
                    "un salto espureo (reinicio del addon, identidad de bateria resuelta "
                    "distinta...). El contador publicado se queda en %.3f y sigue subiendo "
                    "desde ahi, igual que con una bajada.",
                    entity_id, float(anterior_total), float(total), delta, publicado,
                )
            elif delta > 0:
                publicado += delta
            elif delta < 0:
                log.info(
                    "%s: el acumulado interno ha bajado de %.3f a %.3f (correccion). "
                    "El contador publicado se queda en %.3f y sigue subiendo desde ahi "
                    "-- publicar la bajada haria que HA la contase como un reinicio, "
                    "sumando el valor entero de golpe al Panel de Energia.",
                    entity_id, float(anterior_total), float(total), publicado,
                )

        data[entity_id] = {"last_total": float(total), "published": publicado}
        _save(data)
        return publicado


def resync(entity_id: str, total: float) -> None:
    """Realinea sin publicar nada -- para cuando el contador de HA se ha
    reiniciado de verdad y se quiere volver a partir del total interno."""
    with _lock:
        data = _load()
        data[entity_id] = {"last_total": float(total), "published": float(total)}
        _save(data)
