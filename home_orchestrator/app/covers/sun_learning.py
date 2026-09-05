"""
Aprendizaje del % de cierre por proteccion solar, por zona -- mismo
espiritu "sin caja negra" que climate/thermal_model.py: UN solo numero
explicable ("con esta zona, cerrar hasta el X% mantiene la subida de
temperatura por debajo de lo aceptable"), ajustado poco a poco a partir
del propio sensor de temperatura INTERIOR de la zona (`indoor_temp_
sensor`, un `sensor.*` cualquiera) -- deliberadamente NO se apoya en
nada que calcule Climate (ni su `outdoor_forecast` ni su termostato):
a peticion expresa del usuario, esta zona tiene que poder aprender sola,
sin depender de que exista una zona de Climate vinculada a la misma
habitacion ("estamos tirando de lo que crea Climate y deberia de ser lo
mas independiente posible").

Nada de historico ni de solver: cada vez que se llama (misma cadencia
que `reapply_minutes`, el ciclo periodico normal de la zona), se
compara la lectura actual del sensor con la de la ULTIMA vez que se
llamo mientras la proteccion solar seguia activa sin interrupcion --
eso da una pendiente real (°C/hora) de cuanto calienta la zona YA con
la persiana en su posicion de proteccion actual. Si sube mas de lo
aceptable (`sun_protection_max_warming_deg_h`), se cierra un paso mas
para el proximo ciclo; si sube muy poco (sombra de sobra, luz
desperdiciada de balde), se abre un paso. Igual que
`heating_rate_deg_h` en Climate, el resultado es un numero que se
explica en una frase, nunca un modelo opaco.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger("covers.sun_learning")

# Tramo minimo antes de fiarse de una pendiente -- mismo motivo que
# MIN_RUN_MINUTES en thermal_model.py: un par de minutos de ruido del
# sensor no es una pendiente real.
MIN_RUN_MINUTES = 15

MIN_POSITION_PCT = 0
MAX_POSITION_PCT = 100


def update(zone: dict, state: dict, ws, protecting_now: bool, current_target: int) -> int:
    """Devuelve el % de proteccion a usar AHORA MISMO: el aprendido si
    el aprendizaje esta activo y ya hay dato, si no `current_target`
    (el `sun_protection_position_pct` configurado de siempre, ya
    resuelto por quien llama). Actualiza `state` in-place -- se
    persiste igual que el resto del estado de la zona (ver
    ZoneRunner.to_persisted_state)."""
    if not zone.get("auto_learn_sun_protection_enabled", False):
        state.pop("_sun_learn_baseline", None)
        return current_target

    sensor = zone.get("indoor_temp_sensor")
    if not sensor:
        return current_target

    learned = state.get("learned_sun_protection_position_pct")
    if learned is None:
        learned = current_target

    if not protecting_now:
        # Proteccion no activa ahora mismo (de noche, sol no da directo,
        # Climate pidiendo calor...) -- no hay pendiente real que medir
        # mientras tanto, se descarta el punto de referencia para no
        # mezclar un tramo protegido con uno sin proteger.
        state.pop("_sun_learn_baseline", None)
        return learned

    try:
        raw = ws.get_state(sensor)
        temp = float((raw or {}).get("state"))
    except Exception:
        log.debug("Zona covers: sensor interior %s no legible todavia", sensor, exc_info=True)
        return learned

    now = time.time()
    baseline = state.get("_sun_learn_baseline")
    if baseline is None:
        state["_sun_learn_baseline"] = {"ts": now, "temp": temp}
        return learned

    elapsed_h = (now - baseline["ts"]) / 3600
    if elapsed_h * 60 < MIN_RUN_MINUTES:
        return learned  # tramo aun demasiado corto para fiarse de la pendiente

    rate = (temp - baseline["temp"]) / elapsed_h
    max_rate = float(zone.get("sun_protection_max_warming_deg_h", 0.8) or 0.8)
    step = float(zone.get("sun_protection_learn_step_pct", 5) or 5)

    if rate > max_rate:
        learned = max(MIN_POSITION_PCT, learned - step)
        log.info(
            "Zona covers: %s sube %.2f°C/h protegiendo a %s%% (limite %.2f°C/h) -> cierra a %s%%",
            sensor, rate, current_target, max_rate, round(learned),
        )
    elif rate < max_rate * 0.4:
        learned = min(MAX_POSITION_PCT, learned + step)
        log.info(
            "Zona covers: %s solo sube %.2f°C/h protegiendo a %s%% (limite %.2f°C/h) -> abre a %s%%",
            sensor, rate, current_target, max_rate, round(learned),
        )

    learned = round(learned)
    state["learned_sun_protection_position_pct"] = learned
    state["_sun_learn_baseline"] = {"ts": now, "temp": temp}
    return learned
