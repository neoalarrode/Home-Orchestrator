"""
Consumo de cada zona de iluminacion APRENDIDO (peticion del usuario, 5-nov:
"aprendizaje automatico, como siempre, y autocorreccion"): las luces no tienen
medidor propio, asi que se aprende lo que gasta cada una comparando el
consumo de la casa justo antes y justo despues de que se encienda o se
apague -- igual que Climate con los aires (climate/power_model.py).

- Muestra: luz que pasa de apagada a encendida con brillo b (o al reves).
  Salto = mediana del consumo de la casa entre +SETTLE y +AFTER segundos menos
  la mediana entre -BEFORE y -GAP segundos (al reves si se apaga).
- Se descarta si OTRA luz de cualquier zona cambio a menos de GUARD segundos
  (no se sabria de quien es el salto) o si el valor es imposible.
- Autocorreccion: solo cuentan las ultimas MAX_SAMPLES muestras de cada luz y
  las de menos de MAX_AGE_DAYS; se usa la mediana, y las muestras a mas de
  OUTLIER_MAD desviaciones de ella se ignoran. Con brillos variados se ajusta
  una recta potencia = a + b * brillo; si no, la mediana de vatios/brillo.
- Sin MIN_SAMPLES muestras limpias una luz no tiene potencia (None): la zona
  la lista como "sin aprender" y no se inventa nada.
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from collections import deque
from datetime import datetime, timezone

log = logging.getLogger(__name__)

BEFORE_S, GAP_S = 40, 3
SETTLE_S, AFTER_S = 8, 40
PROCESS_AFTER_S = AFTER_S + 10      # cuando ya hay historico de la ventana "despues"
GUARD_S = 60
MIN_SAMPLES = 5
MAX_SAMPLES = 40
MAX_AGE_DAYS = 30
OUTLIER_MAD = 3.5
MAX_LIGHT_W = 300.0
MIN_DELTA_W = 1.0
FIT_MIN_SAMPLES, FIT_MIN_SPREAD = 8, 0.3
ENERGY_MAX_GAP_S = 900
HOME_SENSOR_FALLBACK = "sensor.battery_orchestrator_load"
GRID_SIGNAL_ENTITY_ID = "sensor.battery_orchestrator_grid_signal"

# Cambios de luces de TODAS las zonas del proceso (guarda entre zonas).
_events_lock = threading.Lock()
_events: deque = deque(maxlen=2000)     # (ts, entity_id)


def _note_event(ts: float, entity_id: str) -> None:
    with _events_lock:
        _events.append((ts, entity_id))


def _other_event_near(ts: float, entity_id: str) -> bool:
    with _events_lock:
        return any(e != entity_id and abs(t - ts) <= GUARD_S for t, e in _events)


def _median_between(points: list[tuple[float, float]], t0: float, t1: float) -> float | None:
    """Mediana ponderada por tiempo de una serie escalonada [(ts, valor)]
    entre t0 y t1 (cada valor vale hasta el siguiente punto)."""
    segs: list[tuple[float, float]] = []
    for k, (ts, w) in enumerate(points):
        end = points[k + 1][0] if k + 1 < len(points) else t1
        a, b = max(ts, t0), min(end, t1)
        if b > a:
            segs.append((w, b - a))
    if not segs:
        return None
    segs.sort()
    half, acc = sum(d for _, d in segs) / 2, 0.0
    for v, d in segs:
        acc += d
        if acc >= half:
            return v
    return segs[-1][0]


def _mad_keep(values: list[float]) -> list[bool]:
    med = statistics.median(values)
    mad = statistics.median(abs(x - med) for x in values) or max(0.5, abs(med) * 0.05)
    return [abs(x - med) <= OUTLIER_MAD * mad for x in values]


def _fit(pts: list[tuple[float, float]]) -> tuple[float, float] | None:
    bs = [b for b, _ in pts]
    mb, mw = statistics.fmean(bs), statistics.fmean(w for _, w in pts)
    var = sum((b - mb) ** 2 for b in bs)
    if var <= 0:
        return None
    slope = sum((b - mb) * (w - mw) for b, w in pts) / var
    return mw - slope * mb, slope


def light_power(samples: list, brightness: float) -> float | None:
    """Vatios de una luz con `brightness` (0..1) segun sus muestras
    [(ts, brillo, vatios)], o None si aun no hay bastantes limpias."""
    now = time.time()
    pts = [(b, w) for ts, b, w in samples if now - ts <= MAX_AGE_DAYS * 86400 and b > 0]
    if len(pts) < MIN_SAMPLES:
        return None
    bs = [b for b, _ in pts]
    if len(pts) >= FIT_MIN_SAMPLES and max(bs) - min(bs) >= FIT_MIN_SPREAD:
        # Con brillos variados: recta vatios = a + b * brillo; las muestras que
        # se salen de ella (un horno arrancando a la vez...) se quitan y se rehace.
        line = _fit(pts)
        if line is not None:
            keep = _mad_keep([w - (line[0] + line[1] * b) for b, w in pts])
            clean = [p for p, k in zip(pts, keep) if k]
            line = _fit(clean) if len(clean) >= MIN_SAMPLES else None
            if line is not None:
                value = line[0] + line[1] * brightness
                if value > 0:
                    return round(value, 1)
    per_unit = [w / b for b, w in pts]
    clean = [x for x, k in zip(per_unit, _mad_keep(per_unit)) if k]
    if len(clean) < MIN_SAMPLES:
        return None
    return round(statistics.median(clean) * brightness, 1)


class LightPowerLearner:
    """Una por zona. `state` es un dict persistido con la zona (se modifica en
    sitio): {"samples": {luz: [[ts, brillo, vatios], ...]}, "energy_wh",
    "energy_since"}."""

    def __init__(self, state: dict) -> None:
        self.state = state
        self.state.setdefault("samples", {})
        self.state.setdefault("energy_wh", 0.0)
        self._prev: dict[str, float] = {}
        self._pending: list[dict] = []
        self._last_energy: tuple[float, float] | None = None
        self.power_w: float = 0.0
        self.unlearned: list[str] = []

    # -- observacion ---------------------------------------------------
    def observe(self, now: float, levels: dict[str, float]) -> None:
        """`levels`: brillo actual (0..1, 0 = apagada) de cada luz con dato."""
        for entity_id, level in levels.items():
            prev = self._prev.get(entity_id)
            self._prev[entity_id] = level
            if prev is None or abs(level - prev) < 0.01:
                continue
            _note_event(now, entity_id)
            # Solo encendido/apagado: un cambio de brillo de una luz ya
            # encendida mezclaria la parte fija con la variable.
            if (prev == 0) != (level == 0):
                self._pending.append({"entity_id": entity_id, "ts": now,
                                      "brightness": level or prev, "sign": 1 if level > 0 else -1})

    def process_due(self, now: float, fetch_power) -> None:
        """Procesa las muestras cuya ventana ya ha pasado. `fetch_power(t0, t1)`
        devuelve [(ts, vatios)] del consumo de la casa (o [] si no hay)."""
        keep = []
        for p in self._pending:
            if now - p["ts"] < PROCESS_AFTER_S:
                keep.append(p)
                continue
            self._take_sample(p, fetch_power)
        self._pending = keep

    def _take_sample(self, p: dict, fetch_power) -> None:
        ts, entity_id = p["ts"], p["entity_id"]
        if _other_event_near(ts, entity_id):
            return
        try:
            points = fetch_power(ts - BEFORE_S - 30, ts + AFTER_S)
        except Exception:
            log.debug("Sin historico de consumo para %s", entity_id, exc_info=True)
            return
        before = _median_between(points, ts - BEFORE_S, ts - GAP_S)
        after = _median_between(points, ts + SETTLE_S, ts + AFTER_S)
        if before is None or after is None:
            return
        watts = (after - before) * p["sign"]
        if watts < MIN_DELTA_W or watts > MAX_LIGHT_W:
            return
        samples = self.state["samples"].setdefault(entity_id, [])
        samples.append([round(ts, 1), round(p["brightness"], 3), round(watts, 1)])
        del samples[:-MAX_SAMPLES]

    # -- resultado -----------------------------------------------------
    def update_power(self, now: float, levels: dict[str, float]) -> float:
        """Potencia de la zona AHORA con las luces en `levels`, e integra la
        energia con la potencia que estaba vigente hasta ahora."""
        if self._last_energy is not None and now > self._last_energy[0]:
            wh = self._last_energy[1] * min(now - self._last_energy[0], ENERGY_MAX_GAP_S) / 3600
            if wh > 0:
                if not self.state.get("energy_since"):
                    self.state["energy_since"] = datetime.now(timezone.utc).isoformat()
                self.state["energy_wh"] = float(self.state.get("energy_wh") or 0.0) + wh
        total, unlearned = 0.0, []
        for entity_id, level in levels.items():
            if level <= 0:
                continue
            w = light_power(self.state["samples"].get(entity_id) or [], level)
            if w is None:
                unlearned.append(entity_id)
            else:
                total += w
        self.power_w, self.unlearned = round(total, 1), unlearned
        self._last_energy = (now, self.power_w)
        return self.power_w

    @property
    def energy_kwh(self) -> float:
        return round(float(self.state.get("energy_wh") or 0.0) / 1000, 4)

    def learned(self) -> dict[str, float | None]:
        return {e: light_power(s, 1.0) for e, s in self.state["samples"].items()}


def home_power_sensor(ws) -> str:
    """El sensor de consumo de la casa que ya usa Energy (mismo origen que
    Climate, ver climate/grid_signal.py)."""
    try:
        st = ws.get_state(GRID_SIGNAL_ENTITY_ID) or {}
        sensor = (st.get("attributes") or {}).get("home_power_sensor")
        if sensor:
            return sensor
    except Exception:
        pass
    return HOME_SENSOR_FALLBACK


def history_fetcher(ws, sensor: str):
    def fetch(t0: float, t1: float) -> list[tuple[float, float]]:
        iso = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
        result = ws.call("history/history_during_period", start_time=iso(t0), end_time=iso(t1),
                         entity_ids=[sensor], minimal_response=True, no_attributes=True,
                         significant_changes_only=False)
        out = []
        for p in (result or {}).get(sensor) or []:
            lu = p.get("lu", p.get("last_updated"))
            try:
                out.append((float(lu), float(p.get("s", p.get("state")))))
            except (TypeError, ValueError):
                continue
        return sorted(out)
    return fetch
