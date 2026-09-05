"""
Curva de "ritmo diario" para el ZoneRunner de Covers -- misma forma que
`lighting/schedule.py` usa para el brillo (sube desde el amanecer hasta
el maximo en el mediodia solar, baja hacia el atardecer), aqui aplicada a
la posicion de la persiana en vez de al brillo de una luz.

Deliberadamente PROPIA de este plugin, no importada de `lighting/` --
cada plugin de zonas de este addon mantiene su propia copia pequeña de
este calculo (Climate y Lighting ya hacen lo mismo cada uno por su
lado) en vez de acoplarse al paquete interno de otro plugin.
"""

from __future__ import annotations

from datetime import datetime, timezone

_EVENT_ATTRS = (
    ("rising", "next_rising"),
    ("setting", "next_setting"),
    ("noon", "next_noon"),
    ("midnight", "next_midnight"),
)


def _parse_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def sun_position(attrs: dict, now: datetime | None = None) -> float | None:
    """[-1, 1]: negativo de noche (mas negativo cuanto mas lejos del
    amanecer/atardecer, -1 en la medianoche solar), positivo de dia
    (hasta +1 en el mediodia solar). `None` si `sun.sun` no trae los 4
    atributos esperados ahora mismo."""
    now = now or datetime.now(timezone.utc)
    events: list[tuple[str, float]] = []
    for name, attr in _EVENT_ATTRS:
        dt = _parse_iso(attrs.get(attr))
        if dt is None:
            return None
        ts = dt.timestamp()
        events.append((name, ts))
        events.append((name, ts - 24 * 3600))

    events.sort(key=lambda e: e[1])
    now_ts = now.timestamp()
    idx = 0
    while idx < len(events) and events[idx][1] <= now_ts:
        idx += 1
    if idx == 0 or idx >= len(events):
        return None
    (_, prev_ts), (next_event, next_ts) = events[idx - 1], events[idx]

    h, x = (prev_ts, next_ts) if next_event in ("setting", "rising") else (next_ts, prev_ts)
    if h == x:
        return None
    k = 1.0 if next_event in ("setting", "noon") else -1.0
    return k * (1 - ((now_ts - h) / (h - x)) ** 2)


def day_rhythm_position(position: float, min_pct: float, max_pct: float) -> int:
    """De noche (`position <= 0`) se queda fija en `min_pct` -- no tiene
    sentido "abrir" de madrugada sin que nadie lo vea, mismo criterio que
    el brillo de Lighting de noche. De dia, sube proporcional a
    `position` hasta `max_pct` en el mediodia solar."""
    if position <= 0:
        return round(min_pct)
    return round(min_pct + (max_pct - min_pct) * position)
