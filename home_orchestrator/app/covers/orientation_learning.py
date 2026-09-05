"""
Aprende SOLA -- del propio historico de Home Assistant -- por que rango
de acimut del sol calienta de verdad esta zona, para que el usuario NO
tenga que declarar en grados por donde da cada ventana (HA no guarda esa
orientacion en ningun sitio, ninguna integracion la expone). Mismo
espiritu "sin caja negra" que climate/thermal_model.py: nada de ML ni de
solver, se buscan tramos reales donde TODAS las persianas de la zona
estaban totalmente abiertas (para no confundir "protegida" con
"expuesta"), se agrupan por en que zona de 15° estaba el sol durante
cada tramo, y se compara la MEDIANA de cuanto subio el sensor de
temperatura propio de la zona en cada grupo frente a la mediana general
de la zona -- los grupos claramente por encima son "el sol pega aqui de
verdad". El resultado se explica en una frase: "esta ventana calienta
claramente cuando el sol esta entre 120° y 210°, segun tu propio
historico de los ultimos N dias".

Requiere el mismo `indoor_temp_sensor` que ya usa `sun_learning.py` --
sin sensor propio de la zona no hay NINGUNA señal en HA de la que
deducir la orientacion de una ventana, asi que sin el esto no calcula
nada (nunca se inventa un rango).
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone

log = logging.getLogger("covers.orientation_learning")

HISTORY_DAYS = 10
BIN_DEG = 15
N_BINS = 360 // BIN_DEG

MIN_SAMPLES_PER_BIN = 4
MIN_BINS_WITH_DATA = 3
MIN_DT_MINUTES = 5
MAX_DT_MINUTES = 90
MIN_ELEVATION_FOR_SIGNAL = 5  # el sol muy rasante apenas calienta, no aporta señal util
FULLY_OPEN_THRESHOLD_PCT = 90  # tramo valido solo si TODAS las persianas de la zona estaban asi de abiertas


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _start_iso(days: int) -> str:
    return (_utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_epoch(last_updated) -> float | None:
    if isinstance(last_updated, (int, float)):
        return float(last_updated)
    return None


def _value_at_or_before(points: list[tuple[float, float]], ts: float) -> float | None:
    best = None
    for t, v in points:
        if t <= ts:
            best = v
        else:
            break
    return best


def _numeric_points(raw: list[dict]) -> list[tuple[float, float]]:
    out = []
    for p in raw:
        ts = _to_epoch(p.get("last_updated"))
        if ts is None:
            continue
        try:
            out.append((ts, float(p["state"])))
        except (TypeError, ValueError, KeyError):
            continue
    out.sort(key=lambda pair: pair[0])
    return out


def _sun_points(raw: list[dict]) -> list[tuple[float, float, float]]:
    """`(ts, elevation, azimuth)`, orden cronologico -- ya viene con
    atributos completos por punto (ver docstring de `HAWebSocketClient.
    get_history`, se solicita `with_attributes=True`)."""
    out = []
    for p in raw:
        ts = _to_epoch(p.get("last_updated"))
        attrs = p.get("attributes") or {}
        elevation, azimuth = attrs.get("elevation"), attrs.get("azimuth")
        if ts is None or elevation is None or azimuth is None:
            continue
        out.append((ts, float(elevation), float(azimuth)))
    out.sort(key=lambda tup: tup[0])
    return out


def _cover_open_points(ws, entity_id: str, invert: bool, days: int) -> list[tuple[float, float]]:
    """Posicion (0-100, ya en la convencion normal -- invertida aqui
    mismo si hace falta, igual que `ZoneRunner._current_position`) de
    una persiana a lo largo del tiempo, a partir de su propio
    `current_position` en el historico."""
    try:
        raw = ws.get_history(entity_id, _start_iso(days), with_attributes=True)
    except Exception:
        log.debug("Sin historico de %s todavia", entity_id, exc_info=True)
        return []
    out = []
    for p in raw:
        ts = _to_epoch(p.get("last_updated"))
        pos = (p.get("attributes") or {}).get("current_position")
        if ts is None or pos is None:
            continue
        pos = float(pos)
        if invert:
            pos = 100 - pos
        out.append((ts, pos))
    out.sort(key=lambda pair: pair[0])
    return out


def _bucket(azimuth: float) -> int:
    return int(azimuth // BIN_DEG) % N_BINS


def _largest_contiguous_run(hot_bins: set[int]) -> list[int] | None:
    """Mayor tramo contiguo de indices en el anillo circular de N_BINS
    (el acimut da la vuelta en 360°/0°) -- si TODOS los bins estan
    calientes, se devuelven todos (ventana que recibe sol casi todo el
    dia, caso real de una orientacion muy expuesta)."""
    if not hot_bins:
        return None
    if len(hot_bins) == N_BINS:
        return list(range(N_BINS))
    best: list[int] = []
    for start in hot_bins:
        if (start - 1) % N_BINS in hot_bins:
            continue  # no es el principio de un tramo, ya se cuenta desde otro arranque
        run = [start]
        nxt = (start + 1) % N_BINS
        while nxt in hot_bins and len(run) < N_BINS:
            run.append(nxt)
            nxt = (nxt + 1) % N_BINS
        if len(run) > len(best):
            best = run
    return best


def compute_orientation(ws, zone: dict, days: int = HISTORY_DAYS) -> dict:
    """`{"azimuth_min", "azimuth_max", "reliable", "bins_used"}` -- sin
    `indoor_temp_sensor` declarado, o sin historico suficiente todavia,
    `reliable=False` y los dos primeros a `None` (nunca un rango
    inventado; quien llama debe seguir usando el `window_azimuth_min/
    max` configurado a mano mientras tanto, ver `ZoneRunner.
    _effective_azimuth_range`)."""
    result = {"azimuth_min": None, "azimuth_max": None, "reliable": False, "bins_used": 0}

    sensor = zone.get("indoor_temp_sensor")
    covers = [e for e in (zone.get("cover_entities") or []) if e]
    if not sensor or not covers:
        return result

    try:
        temp_raw = ws.get_history(sensor, _start_iso(days), with_attributes=False)
        sun_raw = ws.get_history("sun.sun", _start_iso(days), with_attributes=True)
    except Exception:
        log.debug("Sin historico suficiente para aprender orientacion todavia", exc_info=True)
        return result

    temp_points = _numeric_points(temp_raw)
    sun_points = _sun_points(sun_raw)
    if not temp_points or len(sun_points) < 2:
        return result

    invert = zone.get("invert_position", False)
    cover_points = {e: _cover_open_points(ws, e, invert, days) for e in covers}

    bins: dict[int, list[float]] = {}
    for i in range(len(sun_points) - 1):
        t0, elev0, az0 = sun_points[i]
        t1, elev1, az1 = sun_points[i + 1]
        if elev0 < MIN_ELEVATION_FOR_SIGNAL:
            continue
        dt_minutes = (t1 - t0) / 60
        if not (MIN_DT_MINUTES <= dt_minutes <= MAX_DT_MINUTES):
            continue

        fully_open = True
        for entity_id in covers:
            pts = cover_points.get(entity_id) or []
            p0 = _value_at_or_before(pts, t0)
            p1 = _value_at_or_before(pts, t1)
            if p0 is None or p1 is None or p0 < FULLY_OPEN_THRESHOLD_PCT or p1 < FULLY_OPEN_THRESHOLD_PCT:
                fully_open = False
                break
        if not fully_open:
            continue

        temp0 = _value_at_or_before(temp_points, t0)
        temp1 = _value_at_or_before(temp_points, t1)
        if temp0 is None or temp1 is None:
            continue

        slope = (temp1 - temp0) / (dt_minutes / 60)
        bucket = _bucket((az0 + az1) / 2)
        bins.setdefault(bucket, []).append(slope)

    medians = {b: statistics.median(v) for b, v in bins.items() if len(v) >= MIN_SAMPLES_PER_BIN}
    if len(medians) < MIN_BINS_WITH_DATA:
        return result

    baseline = statistics.median(medians.values())
    spread = statistics.pstdev(medians.values()) if len(medians) > 1 else 0.0
    threshold = baseline + max(0.3, spread * 0.5)
    hot_bins = {b for b, m in medians.items() if m > threshold}
    if not hot_bins:
        return result

    run = _largest_contiguous_run(hot_bins)
    if not run:
        return result

    result["azimuth_min"] = (min(run) * BIN_DEG) % 360
    result["azimuth_max"] = ((max(run) + 1) * BIN_DEG) % 360 or 360
    result["reliable"] = True
    result["bins_used"] = sum(len(bins[b]) for b in run if b in bins)
    log.info(
        "Zona covers %s: orientacion aprendida -- acimut %s°-%s° (baseline %.2f°C/h, umbral %.2f°C/h, %s bins calientes de %s con dato)",
        zone.get("name"), result["azimuth_min"], result["azimuth_max"], baseline, threshold, len(hot_bins), len(medians),
    )
    return result
