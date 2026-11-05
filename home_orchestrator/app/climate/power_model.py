"""
Aprendizaje de la potencia TIPICA de un actuador CONCRETO (no de toda la
zona: una misma zona puede tener un aire acondicionado sin forma de
medir su consumo real y un radiador con su propio sensor, cada uno con
su propia fuente — ver CONF_ACTUATOR_POWER en const.py), para cuando ese
actuador en concreto no tiene ni sensor propio ni un valor fijo
declarado. Util sobre todo para equipos donde no se puede instrumentar
el consumo real, como un aire acondicionado con maquina exterior
COMPARTIDA entre varias interiores.

Igual que thermal_model.py: nada de aprendizaje automatico opaco. Se
busca en el historico de un sensor de potencia GENERAL de la vivienda
(CONF_HOME_POWER_SENSOR) el salto medido justo antes/despues de cada vez
que este actuador concreto cambia de estado, y se toma la MEDIANA de
esos saltos — un numero que se puede explicar en una frase: "de media,
este actuador sube el consumo de la vivienda en 1200W al encenderse".
Sin historico suficiente, `reliable=False` — climate.py se encarga de
caer entonces a la potencia estimada fija declarada para ese actuador
(ver CONF_ACTUATOR_POWER en const.py) si la hay, o a nada.

MEJORA CLAVE para el caso de maquina exterior compartida: antes de
aceptar una transicion como muestra valida, se comprueba si ALGUNA OTRA
zona Climate Orchestrator (sus propios switches/climate.*/humidifier.*
declarados) ya estaba activa en ese mismo instante — si es asi, esa
transicion se descarta (no se puede aislar limpiamente que parte del
salto es de esta zona y cual de la otra). Solo se cuentan transiciones
"limpias": ningun otro equipo conocido cambiando ni ya encendido en la
ventana de muestreo. Esto es justo lo que hace falta para un split
multi-interior con un unico compresor: si dos interiores comparten
maquina exterior, un salto medido mientras la otra ya estaba en marcha
no dice nada fiable sobre el consumo de la primera.

Sigue siendo una CORRELACION, no una medida directa — `reliable=False`
con pocas muestras limpias, nunca se presenta como un dato medido de
verdad.
"""

from __future__ import annotations

import logging
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timedelta, timezone

_LOGGER = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _start_iso(days: int) -> str:
    return (_utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_dt(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)

MIN_RUN_MINUTES = 3            # la potencia cambia al instante, no hace falta un tramo largo como en thermal_model
MIN_VALID_SAMPLES = 4          # transiciones LIMPIAS minimas antes de fiarse de la mediana
SAMPLE_WINDOW_MINUTES = 2      # cuanto se promedia el sensor de potencia justo antes/despues de la transicion
MIN_DELTA_W = 10               # saltos menores se descartan como ruido de medida, no un cambio real

# `_active_intervals` escanea el historico de TODOS los actuadores de
# TODAS las demas zonas — el mismo escaneo, repetido entero, cada vez que
# CUALQUIER zona necesita aprender su potencia. Con varias zonas, aunque
# cada una ya reparte CUANDO recalcula (ver climate.py `_zone_stagger_
# seconds`), pueden seguir coincidiendo dentro de una franja de varios
# minutos — y el resultado ("que tramos estuvo encendido cada actuador
# ajeno en los ultimos N dias") no cambia de un minuto para otro. Se
# cachea a nivel de proceso (compartido entre TODAS las zonas, no por
# zona) para no repetir el mismo escaneo caro varias veces seguidas.
_active_intervals_cache: dict[tuple, tuple[float, list]] = {}
ACTIVE_INTERVALS_CACHE_SECONDS = 1800  # 30 min


class _SyntheticState:
    __slots__ = ("state", "last_changed")

    def __init__(self, state: str, last_changed: datetime) -> None:
        self.state = state
        self.last_changed = last_changed


# LIMITE DURO, no negociable — mismo motivo y mismo valor que en
# thermal_model.py (ver ahi para el razonamiento completo): esta funcion
# TAMBIEN se usa para leer el sensor general de potencia de la casa
# (home_power_sensor), que suele ser de los sensores mas parlanchines de
# toda la instalacion.
MAX_STATES_PER_ENTITY = 5000


def _cap_states(states: list) -> list:
    if len(states) <= MAX_STATES_PER_ENTITY:
        return states
    step = len(states) / MAX_STATES_PER_ENTITY
    return [states[int(i * step)] for i in range(MAX_STATES_PER_ENTITY)]


def _history_for(ws, entity_id: str, days: int) -> list:
    try:
        raw = ws.get_history(entity_id, _start_iso(days), with_attributes=False)
    except Exception:
        _LOGGER.debug("Sin historico (via WebSocket) de %s todavia", entity_id, exc_info=True)
        raw = []
    points = [_SyntheticState(p["state"], _to_dt(p["last_updated"])) for p in raw if p.get("last_updated") is not None]
    return _cap_states(points)


def _state_runs(states: list) -> list[tuple[datetime, datetime, str]]:
    runs = []
    for i, s in enumerate(states):
        if s.state not in ("on", "off"):
            continue
        start = s.last_changed
        end = states[i + 1].last_changed if i + 1 < len(states) else _utcnow()
        runs.append((start, end, s.state))
    return runs


def _climate_on_off_runs(ws, entity_id: str, days: int) -> list[tuple[datetime, datetime, str]]:
    """Traduce el historico de un climate.* a on/off via su `hvac_action`
    (heating/cooling frente al resto) — mismo truco que thermal_model.py."""
    try:
        raw = ws.get_history(entity_id, _start_iso(days), with_attributes=True)
    except Exception:
        _LOGGER.debug("Sin historico (via WebSocket) de %s todavia", entity_id, exc_info=True)
        raw = []
    raw = _cap_states([p for p in raw if p.get("last_updated") is not None])
    synthetic = [
        _SyntheticState(
            "on" if (p.get("attributes") or {}).get("hvac_action") in ("heating", "cooling") else "off",
            _to_dt(p["last_updated"]),
        )
        for p in raw
    ]
    return _state_runs(synthetic)


def _entity_runs(ws, entity_id: str, days: int) -> list[tuple[datetime, datetime, str]]:
    if entity_id.startswith("climate."):
        return _climate_on_off_runs(ws, entity_id, days)
    return _state_runs(_history_for(ws, entity_id, days))


def _other_zone_entities(zones: list[dict], this_zone_id: str) -> list[str]:
    """Actuadores declarados en TODAS las demas zonas Climate — para poder
    descartar transiciones contaminadas por otro equipo compartiendo la
    misma maquina exterior. `zones`: config de TODAS las zonas de este
    plugin (ver config_store), pasada explicita por quien llama en vez de
    leerse de `hass.config_entries` (ya no hay config_entries fuera de
    HA Core) — el plugin de Climate mantiene su propia lista."""
    others: list[str] = []
    for zone in zones:
        if zone.get("id") == this_zone_id:
            continue
        others.extend(zone.get("heat_switches") or [])
        others.extend(zone.get("cool_switches") or [])
        others.extend(zone.get("climate_entities") or [])
        others.extend(zone.get("humidifier_entities") or [])
    return others


def _active_intervals(ws, entities: list[str], days: int) -> list[tuple[datetime, datetime]]:
    """Tramos (inicio, fin) en los que CUALQUIERA de estas entidades
    estuvo encendida/calentando/enfriando — la union de todas, para
    comprobar solapamiento rapido despues."""
    intervals = []
    for entity_id in entities:
        for run_start, run_end, state in _entity_runs(ws, entity_id, days):
            if state == "on":
                intervals.append((run_start, run_end))
    return intervals


def _cached_active_intervals(ws, entities: list[str], days: int) -> list[tuple[datetime, datetime]]:
    """Version cacheada de `_active_intervals` (ver ACTIVE_INTERVALS_CACHE_
    SECONDS arriba) — la clave es solo el conjunto de entidades (no start/
    end, que cambian en cada llamada por segundos): dentro de la ventana de
    cache, se asume que "hace cuanto que paso cada N dias" no varia lo
    suficiente como para justificar repetir el escaneo."""
    if not entities:
        return []
    key = tuple(sorted(entities))
    now_ts = time.time()
    cached = _active_intervals_cache.get(key)
    if cached is not None and (now_ts - cached[0]) < ACTIVE_INTERVALS_CACHE_SECONDS:
        return cached[1]
    result = _active_intervals(ws, entities, days)
    _active_intervals_cache[key] = (now_ts, result)
    return result


def _overlaps(intervals: list[tuple[datetime, datetime]], t0: datetime, t1: datetime) -> bool:
    return any(a < t1 and b > t0 for a, b in intervals)


def _avg_around(power_states: list, center_ts: datetime, before: bool, window_minutes: float) -> float | None:
    vals = []
    for s in power_states:
        try:
            v = float(s.state)
        except (TypeError, ValueError):
            continue
        delta_min = (center_ts - s.last_changed).total_seconds() / 60 if before \
            else (s.last_changed - center_ts).total_seconds() / 60
        if 0 <= delta_min <= window_minutes:
            vals.append(v)
    return statistics.mean(vals) if vals else None


_EMPTY_ENTRY = {"learned_power_w": None, "reliable": False, "samples_used": 0, "samples_discarded_other_zone": 0}


def _learn_one_entity(ws, entity_id: str, days: int, other_intervals: list, power_states: list) -> dict:
    model = dict(_EMPTY_ENTRY)
    runs = _entity_runs(ws, entity_id, days)
    if not runs:
        return model

    deltas: list[float] = []
    discarded_other_zone = 0
    for run_start, run_end, _state in runs:
        dur_min = (run_end - run_start).total_seconds() / 60
        if dur_min < MIN_RUN_MINUTES:
            continue

        window_start = run_start - timedelta(minutes=SAMPLE_WINDOW_MINUTES)
        window_end = run_start + timedelta(minutes=SAMPLE_WINDOW_MINUTES)
        if other_intervals and _overlaps(other_intervals, window_start, window_end):
            discarded_other_zone += 1
            continue

        before = _avg_around(power_states, run_start, before=True, window_minutes=SAMPLE_WINDOW_MINUTES)
        after = _avg_around(power_states, run_start, before=False, window_minutes=SAMPLE_WINDOW_MINUTES)
        if before is None or after is None:
            continue
        delta = abs(after - before)
        if delta >= MIN_DELTA_W:
            deltas.append(delta)

    model["samples_discarded_other_zone"] = discarded_other_zone
    if len(deltas) < MIN_VALID_SAMPLES:
        return model

    model["learned_power_w"] = round(statistics.median(deltas), 0)
    model["reliable"] = True
    model["samples_used"] = len(deltas)
    return model


def _compute_power_model_sync(ws, entities: list[str], zone_id: str, zones: list[dict],
                               home_power_sensor: str, days: int) -> dict:
    """Aprende, para CADA entidad de `entities` (las de esta zona sin
    sensor propio ni valor fijo declarado), su salto tipico de potencia —
    ver `_learn_one_entity`. Las demas zonas se consultan UNA vez y se
    reutilizan para todas las entidades de esta zona (mas barato que
    repetir la consulta por cada una)."""
    result: dict[str, dict] = {}
    if not entities or not home_power_sensor:
        return result

    other_entities = _other_zone_entities(zones, zone_id)
    other_intervals = _cached_active_intervals(ws, other_entities, days) if other_entities else []
    power_states = _history_for(ws, home_power_sensor, days)

    for entity_id in entities:
        result[entity_id] = _learn_one_entity(ws, entity_id, days, other_intervals, power_states)
    return result


POWER_MODEL_COMPUTE_TIMEOUT_SECONDS = 60  # limite duro de espera, mismo motivo que thermal_model.py

_power_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="power_model")


def get_power_model(
    ws, entities: list[str], zone_id: str, zones: list[dict], home_power_sensor: str, days: int,
    fallback: dict | None = None,
) -> dict:
    """Consulta el historico por WebSocket en su propio hilo. Devuelve
    {entity_id: {"learned_power_w", "reliable", "samples_used",
    "samples_discarded_other_zone"}} — solo para las entidades pedidas
    (las que de verdad necesitan aprenderse). `ws`: instancia de
    ha_websocket.HAWebSocketClient ya conectada. `zones`: config de TODAS
    las zonas de este plugin (para `_other_zone_entities`).

    Timeout de POWER_MODEL_COMPUTE_TIMEOUT_SECONDS: mismo motivo que en
    thermal_model.py — un calculo que tarda demasiado deja de bloquear a
    esta zona, aunque el hilo siga corriendo por detras hasta terminar
    solo.

    `fallback`: que devolver si falla o se agota el tiempo — quien llama
    puede pasar aqui el modelo actual, para no perder un aprendizaje ya
    fiable por un timeout puntual (mismo razonamiento en
    thermal_model.get_model)."""
    if not entities or not home_power_sensor:
        return {}
    future = _power_executor.submit(_compute_power_model_sync, ws, entities, zone_id, zones, home_power_sensor, days)
    try:
        return future.result(timeout=POWER_MODEL_COMPUTE_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        _LOGGER.warning(
            "Aprendizaje de potencia de %s actuadores superó %ss, se sigue con lo ya aprendido",
            len(entities), POWER_MODEL_COMPUTE_TIMEOUT_SECONDS,
        )
    except Exception:
        _LOGGER.debug("No se pudo aprender la potencia de %s actuadores todavia", len(entities), exc_info=True)
    return fallback if fallback is not None else {}


# ---------------------------------------------------------------------------
# Potencia por ZONA a partir del registro de acciones propio de Climate
# ---------------------------------------------------------------------------
#
# Los delegados de puente (tuya:<id>) no tienen historico en HA, asi que el
# aprendizaje por entidad de arriba nunca aprende nada para ellos. Pero Climate
# SI sabe cuando arranca cada zona a calentar/enfriar (su `_action_log`,
# persistido), y el consumo total de la casa esta en HA. El salto del consumo
# de la casa justo al arrancar la zona es la potencia real de la zona.
ZONE_SAMPLE_BEFORE_MINUTES = 3   # base: media de los 3 min previos al arranque
ZONE_SAMPLE_SETTLE_MINUTES = 1   # el inverter tarda en subir: se salta el 1er minuto
ZONE_SAMPLE_AFTER_MINUTES = 6    # ...y se promedia hasta el minuto 6
ZONE_OTHER_START_GUARD_MINUTES = 7  # arranque de OTRA zona tan cerca: muestra contaminada
ZONE_LEARN_DAYS = 14             # el registro de acciones cubre semanas; una zona puede no arrancar en dias
ZONE_MAX_SAMPLES = 30            # los arranques mas recientes: 30 consultas pequenas cada recalculo


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _power_window(ws, sensor: str, t0: datetime, t1: datetime) -> list:
    """Consumo de la casa SOLO entre t0 y t1, a resolucion completa (sin el
    recorte a MAX_STATES_PER_ENTITY que sufre un historico de dias de un sensor
    tan parlanchin). Incluye el valor vigente en t0."""
    try:
        result = ws.call(
            "history/history_during_period",
            start_time=_iso(t0), end_time=_iso(t1), entity_ids=[sensor],
            minimal_response=True, no_attributes=True, significant_changes_only=False,
        )
    except Exception:
        _LOGGER.debug("Sin historico de %s entre %s y %s", sensor, t0, t1, exc_info=True)
        return []
    points = []
    for p in (result or {}).get(sensor) or []:
        lu = p.get("lu", p.get("last_updated"))
        if lu is None:
            continue
        points.append(_SyntheticState(p.get("s", p.get("state")), _to_dt(float(lu))))
    return sorted(points, key=lambda s: s.last_changed)


def _avg_between(power_states: list, t0: datetime, t1: datetime, include_end: bool = True) -> float | None:
    """Media del consumo entre t0 y t1 PONDERADA POR TIEMPO: un sensor que
    informa al cambiar deja pocos puntos, y cada valor vale mientras dura."""
    total = weight = 0.0
    current = None
    for s in power_states:
        if s.last_changed <= t0:
            current = s
            continue
        if s.last_changed > t1 or (not include_end and s.last_changed >= t1):
            break
        if current is not None:
            try:
                v = float(current.state)
                seg = (s.last_changed - max(current.last_changed, t0)).total_seconds()
                total += v * seg; weight += seg
            except (TypeError, ValueError):
                pass
        current = s
    if current is not None:
        try:
            v = float(current.state)
            seg = (t1 - max(current.last_changed, t0)).total_seconds()
            if seg > 0:
                total += v * seg; weight += seg
        except (TypeError, ValueError):
            pass
    return total / weight if weight > 0 else None


def _zone_starts(action_log: list, days: int, with_detail: bool = False) -> list:
    """Instantes en que la zona pasa de no actuar a calentar/enfriar. Con
    `with_detail`, (instante, modo, ts) para cruzarlo con el exterior."""
    since = _utcnow() - timedelta(days=days)
    starts, prev = [], "off"
    for ts, act in action_log:
        when = _to_dt(ts)
        # "hold" (bomba de calor modulando en banda) ya esta consumiendo: pasar de
        # hold a calentar no es un arranque.
        if act in ("heat", "cool") and prev not in ("heat", "cool", "hold", "dry") and when >= since:
            starts.append((when, act, ts) if with_detail else when)
        prev = act
    return starts


# Potencia por MODO y segun el EXTERIOR (decision del usuario, 4-nov): una bomba
# de calor gasta distinto calentando que enfriando, y distinto con 0 C que con
# 12 C fuera. Con suficientes arranques a exteriores distintos se ajusta una
# recta potencia = a + b * exterior; si no, la mediana de ese modo.
MODE_MIN_SAMPLES = 3
FIT_MIN_SAMPLES = 6
FIT_MIN_SPREAD_C = 4.0
FIT_EXTRAPOLATE_C = 3.0          # fuera de lo visto, como mucho esto mas alla
FIT_CLAMP = (0.5, 2.0)           # la recta nunca se aleja tanto de la mediana


def _fit_mode(samples: list[tuple[float, float | None]]) -> dict:
    """samples = [(vatios, exterior | None)] de un modo."""
    watts = [w for w, _ in samples]
    out = {"median_w": round(statistics.median(watts), 0), "samples": len(watts),
           "reliable": len(watts) >= MODE_MIN_SAMPLES}
    pts = [(t, w) for w, t in samples if t is not None]
    if len(pts) >= FIT_MIN_SAMPLES:
        ts = [t for t, _ in pts]
        spread = max(ts) - min(ts)
        if spread >= FIT_MIN_SPREAD_C:
            mt, mw = statistics.fmean(ts), statistics.fmean(w for _, w in pts)
            var = sum((t - mt) ** 2 for t in ts)
            b = sum((t - mt) * (w - mw) for t, w in pts) / var if var > 0 else 0.0
            out.update(a=round(mw - b * mt, 1), b=round(b, 2), t_min=min(ts), t_max=max(ts))
    return out


def zone_power_at(model: dict | None, mode: str, outdoor: float | None) -> float | None:
    """Potencia aprendida de la zona en `mode` con ese exterior, o None si ese
    modo aun no tiene arranques suficientes."""
    m = ((model or {}).get("by_mode") or {}).get(mode)
    if not m or not m.get("reliable"):
        return None
    median = float(m["median_w"])
    if outdoor is None or "b" not in m:
        return median
    t = min(max(float(outdoor), m["t_min"] - FIT_EXTRAPOLATE_C), m["t_max"] + FIT_EXTRAPOLATE_C)
    value = m["a"] + m["b"] * t
    return round(min(max(value, median * FIT_CLAMP[0]), median * FIT_CLAMP[1]), 0)


def _learn_zone_power_sync(ws, action_log: list, home_power_sensor: str, days: int,
                           other_starts: list[float], outdoor_at: dict | None = None) -> dict:
    model = {"learned_power_w": None, "reliable": False, "samples_used": 0, "samples_discarded_other_zone": 0}
    detail = _zone_starts(action_log, max(days, ZONE_LEARN_DAYS), with_detail=True)
    starts = [d[0] for d in detail]
    mode_of = {d[0]: (d[1], (outdoor_at or {}).get(d[2])) for d in detail}
    by_mode: dict[str, list] = {}
    if len(starts) < MIN_VALID_SAMPLES or not home_power_sensor:
        return model
    others = [_to_dt(t) for t in other_starts]
    guard = timedelta(minutes=ZONE_OTHER_START_GUARD_MINUTES)
    deltas, discarded = [], 0
    for start in starts[-ZONE_MAX_SAMPLES:]:
        if any(abs(o - start) <= guard for o in others):
            discarded += 1
            continue
        t0 = start - timedelta(minutes=ZONE_SAMPLE_BEFORE_MINUTES)
        t_end = start + timedelta(minutes=ZONE_SAMPLE_AFTER_MINUTES)
        window = _power_window(ws, home_power_sensor, t0, t_end)
        # Base estrictamente ANTES del arranque (el instante del arranque ya puede
        # llevar la potencia de la zona y falsearia la base).
        before = _avg_between(window, t0, start, include_end=False)
        after = _avg_between(window, start + timedelta(minutes=ZONE_SAMPLE_SETTLE_MINUTES), t_end)
        if before is None or after is None:
            continue
        delta = after - before
        if delta >= MIN_DELTA_W:
            deltas.append(delta)
            mode, outdoor = mode_of.get(start, ("heat", None))
            by_mode.setdefault(mode, []).append((delta, outdoor))
    model["samples_discarded_other_zone"] = discarded
    model["by_mode"] = {mode: _fit_mode(samples) for mode, samples in by_mode.items()}
    if len(deltas) >= MIN_VALID_SAMPLES:
        model["learned_power_w"] = round(statistics.median(deltas), 0)
        model["reliable"] = True
        model["samples_used"] = len(deltas)
    return model


def learn_zone_power(ws, action_log: list, home_power_sensor: str, days: int,
                     other_starts: list[float] | None = None, fallback: dict | None = None,
                     outdoor_at: dict | None = None) -> dict:
    """Potencia tipica de la ZONA al calentar/enfriar, aprendida del salto del
    consumo de la casa en cada arranque registrado por Climate (mediana, minimo
    MIN_VALID_SAMPLES arranques limpios). Mismo hilo/timeout que get_power_model."""
    if not action_log or not home_power_sensor:
        return fallback or {}
    future = _power_executor.submit(_learn_zone_power_sync, ws, list(action_log), home_power_sensor, days,
                                    list(other_starts or []), dict(outdoor_at or {}))
    try:
        result = future.result(timeout=POWER_MODEL_COMPUTE_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        _LOGGER.warning("Aprendizaje de potencia de zona superó %ss, se sigue con lo ya aprendido",
                        POWER_MODEL_COMPUTE_TIMEOUT_SECONDS)
        return fallback or {}
    except Exception:
        _LOGGER.debug("No se pudo aprender la potencia de la zona todavia", exc_info=True)
        return fallback or {}
    # No perder un aprendizaje fiable por una ventana de historico peor.
    if not result.get("reliable") and (fallback or {}).get("reliable"):
        return fallback
    return result
