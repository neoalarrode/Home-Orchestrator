"""
Consumo de cada zona de iluminacion APRENDIDO (peticion del usuario, 5-nov:
"aprendizaje automatico, como siempre, y autocorreccion"): las luces no tienen
medidor propio, asi que se aprende lo que gasta cada una comparando el
consumo de la casa justo antes y justo despues de que se encienda o se
apague -- igual que Climate con los aires (climate/power_model.py).

- Muestra: una o VARIAS luces de la zona que se encienden/apagan a la vez
  (a menos de CLUSTER_S entre ellas: en casa las luces de una zona van casi
  siempre en grupo, 5-nov). Salto = mediana del consumo de la casa entre
  +SETTLE y +AFTER segundos menos la mediana entre -BEFORE y -GAP segundos.
  Cada muestra es una ecuacion  sum(P_luz * brillo_luz * signo) = salto, y
  con las ultimas muestras se resuelve P de cada luz por minimos cuadrados no
  negativos. Luces que siempre van juntas no se pueden separar entre si, pero
  la suma de la zona sale bien igual.
- Se descarta si una luz de OTRA zona cambio a menos de GUARD segundos (no se
  sabria de quien es el salto) o si el valor es imposible.
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
CLUSTER_S = 5
STABLE_SPREAD_W = 25.0   # la casa tiene que estar quieta antes y despues (p10-p90)
MIN_SAMPLES = 5
MAX_SAMPLES = 120     # muestras de grupo de la zona (varias luces cada una)
MAX_AGE_DAYS = 30
OUTLIER_MAD = 3.5
MAX_LIGHT_W = 300.0
MIN_DELTA_W = 1.0
ENERGY_MAX_GAP_S = 900
# Calibracion forzada (ZoneRunner.calibrate_power): ciclos encendido/apagado.
CALIBRATION_CYCLES, CALIBRATION_HALF_S, CALIBRATION_SKIP_S = 9, 40, 12
HOME_SENSOR_FALLBACK = "sensor.battery_orchestrator_load"
GRID_SIGNAL_ENTITY_ID = "sensor.battery_orchestrator_grid_signal"

# Cambios de luces de TODAS las zonas del proceso (guarda entre zonas).
_events_lock = threading.Lock()
_events: deque = deque(maxlen=2000)     # (ts, entity_id)


def _note_event(ts: float, entity_id: str) -> None:
    with _events_lock:
        _events.append((ts, entity_id))


def _other_event_near(ts: float, own: set[str]) -> bool:
    """Cambio de una luz que NO es de este grupo cerca de `ts`."""
    with _events_lock:
        return any(e not in own and abs(t - ts) <= GUARD_S for t, e in _events)


def _spread_between(points: list[tuple[float, float]], t0: float, t1: float) -> float | None:
    """p90 - p10 (ponderado por tiempo) del consumo entre t0 y t1."""
    segs = []
    for k, (ts, w) in enumerate(points):
        end = points[k + 1][0] if k + 1 < len(points) else t1
        a, b = max(ts, t0), min(end, t1)
        if b > a:
            segs.append((w, b - a))
    if not segs:
        return None
    segs.sort()
    total = sum(d for _, d in segs)
    def q(frac):
        acc = 0.0
        for v, d in segs:
            acc += d
            if acc >= frac * total:
                return v
        return segs[-1][0]
    return q(0.9) - q(0.1)


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


def _nnls(rows: list[dict[str, float]], targets: list[float], iters: int = 400) -> dict[str, float]:
    """Minimos cuadrados NO negativos por descenso de coordenadas (pocas luces
    por zona: no hace falta nada mas). rows[k] = {luz: coeficiente}."""
    lights = sorted({e for r in rows for e in r})
    p = {e: 0.0 for e in lights}
    norm = {e: sum(r.get(e, 0.0) ** 2 for r in rows) for e in lights}
    resid = list(targets)
    for _ in range(iters):
        moved = 0.0
        for e in lights:
            if norm[e] <= 0:
                continue
            g = sum(r.get(e, 0.0) * resid[k] for k, r in enumerate(rows))
            new = max(0.0, p[e] + g / norm[e])
            d = new - p[e]
            if d:
                for k, r in enumerate(rows):
                    c = r.get(e, 0.0)
                    if c:
                        resid[k] -= c * d
                p[e] = new
                moved = max(moved, abs(d))
        if moved < 1e-3:
            break
    return p


def solve_lights(samples: list) -> dict[str, float]:
    """Vatios al 100 % de cada luz segun las muestras [(ts, {luz: coef}, vatios)].
    Solo muestras recientes; las que mas se alejan del ajuste (un horno que
    arranca a la vez...) se quitan y se rehace. Luces con menos de MIN_SAMPLES
    apariciones: sin dato (no se inventa)."""
    now = time.time()
    data = [(c, w) for ts, c, w in samples if now - ts <= MAX_AGE_DAYS * 86400 and c]
    if not data:
        return {}
    rows, targets = [c for c, _ in data], [w for _, w in data]
    p = _nnls(rows, targets)
    resid = [t - sum(p[e] * v for e, v in r.items()) for r, t in zip(rows, targets)]
    if len(resid) >= MIN_SAMPLES:
        med = statistics.median(resid)
        mad = statistics.median(abs(x - med) for x in resid) or max(0.5, abs(statistics.median(targets)) * 0.05)
        keep = [abs(x - med) <= OUTLIER_MAD * mad for x in resid]
        if sum(keep) >= MIN_SAMPLES and not all(keep):
            rows = [r for r, k in zip(rows, keep) if k]
            targets = [t for t, k in zip(targets, keep) if k]
            p = _nnls(rows, targets)
    seen = {e: sum(1 for r in rows if r.get(e)) for e in p}
    return {e: round(w, 1) for e, w in p.items() if seen[e] >= MIN_SAMPLES and w <= MAX_LIGHT_W}


class LightPowerLearner:
    """Una por zona. `state` es un dict persistido con la zona (se modifica en
    sitio): {"samples": {luz: [[ts, brillo, vatios], ...]}, "energy_wh",
    "energy_since"}."""

    def __init__(self, state: dict) -> None:
        self.state = state
        # Formato anterior (muestras por luz suelta, 5-nov): se descarta.
        if not isinstance(self.state.get("groups"), list):
            self.state["groups"] = []
        self.state.pop("samples", None)
        self.state.setdefault("energy_wh", 0.0)
        self._prev: dict[str, float] = {}
        self._pending: list[dict] = []
        self._last_energy: tuple[float, float] | None = None
        self._solved: dict[str, float] = solve_lights(self.state["groups"])
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
            if (prev == 0) == (level == 0):
                continue
            coef = (level or prev) * (1 if level > 0 else -1)
            group = self._pending[-1] if self._pending else None
            if group is not None and now - group["ts"] <= CLUSTER_S and entity_id not in group["coef"]:
                group["coef"][entity_id] = coef
            else:
                self._pending.append({"ts": now, "coef": {entity_id: coef}})

    def process_due(self, now: float, fetch_power) -> None:
        """Procesa las muestras cuya ventana ya ha pasado. `fetch_power(t0, t1)`
        devuelve [(ts, vatios)] del consumo de la casa (o [] si no hay)."""
        keep, added = [], False
        for p in self._pending:
            if now - p["ts"] < PROCESS_AFTER_S:
                keep.append(p)
                continue
            added = self._take_sample(p, fetch_power) or added
        self._pending = keep
        if added:
            self._solved = solve_lights(self.state["groups"])

    def _take_sample(self, p: dict, fetch_power) -> bool:
        ts, coef = p["ts"], p["coef"]
        if _other_event_near(ts, set(coef)):
            return False
        try:
            points = fetch_power(ts - BEFORE_S - 30, ts + CLUSTER_S + AFTER_S)
        except Exception:
            log.debug("Sin historico de consumo para %s", list(coef), exc_info=True)
            return False
        win_before = (ts - BEFORE_S, ts - GAP_S)
        win_after = (ts + CLUSTER_S + SETTLE_S, ts + CLUSTER_S + AFTER_S)
        before, after = _median_between(points, *win_before), _median_between(points, *win_after)
        if before is None or after is None:
            return False
        # Con otro aparato moviendo el consumo (lavavajillas, horno...) el salto
        # de unas luces de pocos vatios se pierde en el ruido: fuera.
        if max(_spread_between(points, *win_before) or 0.0, _spread_between(points, *win_after) or 0.0) > STABLE_SPREAD_W:
            return False
        delta = after - before
        # Mismo signo que el grupo (todas encendiendo -> sube) y en rango.
        expected_sign = 1 if sum(coef.values()) > 0 else -1
        if delta * expected_sign < MIN_DELTA_W or abs(delta) > MAX_LIGHT_W * len(coef):
            return False
        groups = self.state["groups"]
        groups.append([round(ts, 1), {e: round(c, 3) for e, c in coef.items()}, round(delta, 1)])
        del groups[:-MAX_SAMPLES]
        return True

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
            full = self._solved.get(entity_id)
            if full is None:
                unlearned.append(entity_id)
            else:
                total += full * level
        self.power_w, self.unlearned = round(total, 1), unlearned
        self._last_energy = (now, self.power_w)
        return self.power_w

    @property
    def energy_kwh(self) -> float:
        return round(float(self.state.get("energy_wh") or 0.0) / 1000, 4)

    def learned(self) -> dict[str, float]:
        return dict(self._solved)


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
