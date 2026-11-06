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
MIN_SAMPLES = 15      # con el ruido de la casa hacen falta muchas muestras: se aprende con el tiempo
MAX_SAMPLES = 400     # muestras de grupo de la zona (varias luces cada una)
MAX_AGE_DAYS = 30
OUTLIER_MAD = 3.5
MAX_LIGHT_W = 300.0
MIN_DELTA_W = 1.0
# Regularizacion (en "muestras") del ajuste: el fijo pesa mas que la parte
# proporcional, asi con datos solo al 100 % casi todo va a la parte
# proporcional (LED ~ lineal) y con datos solo al 1 % el fijo se lo lleva todo.
RIDGE_BASE, RIDGE_LEVEL = 1.0, 0.25
BASE_SUFFIX = "#fijo"
LEVEL_HISTORY_S = 180
ENERGY_MAX_GAP_S = 900
# Calibracion forzada (ZoneRunner.calibrate_power), por SALTOS (6-nov): la casa
# no esta quieta (aire, baterias, otros aparatos), pero en unos segundos casi no
# se mueve. Cada encendido/apagado compara la mediana del contador STEP_BEFORE_S
# antes con la de [STEP_AFTER_FROM_S, STEP_AFTER_TO_S] despues (las bombillas
# Matter/Govee tardan algun segundo en responder); muchos saltos y mediana.
CALIBRATION_CYCLES = 15            # 30 saltos (encender + apagar)
CALIBRATION_HALF_S = 12.0
STEP_BEFORE_S = 3.0
STEP_AFTER_FROM_S, STEP_AFTER_TO_S = 4.0, 11.0
STEP_OTHER_GUARD_S = (5.0, 12.0)   # luz de otra zona en [-5, +12] s: salto descartado
METER_POLL_S = 0.5
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


def _solve_linear(a: list[list[float]], b: list[float]) -> list[float] | None:
    """Gauss con pivote parcial (matrices pequenas: 2 parametros por luz)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f:
                for k in range(col, n + 1):
                    m[r][k] -= f * m[col][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def _nnls(rows: list[dict[str, float]], targets: list[float],
          ridge: dict[str, float] | None = None) -> dict[str, float]:
    """Minimos cuadrados NO negativos con una pequena regularizacion (`ridge`
    por parametro, en "muestras"): con ella, luces que SIEMPRE van juntas se
    reparten el consumo a partes iguales en vez de cargarlo todo en una (Cocina,
    6-nov: 19,5 W en una barra y 0 en las otras cuatro). Conjunto activo
    resolviendo las ecuaciones normales: pocas incognitas por zona."""
    names = sorted({e for r in rows for e in r})
    if not names:
        return {}
    idx = {e: i for i, e in enumerate(names)}
    n = len(names)
    g = [[0.0] * n for _ in range(n)]
    h = [0.0] * n
    for r, t in zip(rows, targets):
        items = [(idx[e], c) for e, c in r.items() if c]
        for i, ci in items:
            h[i] += ci * t
            for j, cj in items:
                g[i][j] += ci * cj
    for e, i in idx.items():
        g[i][i] += (ridge or {}).get(e, RIDGE_LEVEL)
    free = set(range(n))
    x = [0.0] * n
    for _ in range(4 * n + 10):
        f = sorted(free)
        sol = _solve_linear([[g[i][j] for j in f] for i in f], [h[i] for i in f]) if f else []
        if sol is None:
            break
        x = [0.0] * n
        for k, i in enumerate(f):
            x[i] = sol[k]
        neg = [i for i in f if x[i] < 0]
        if neg:
            free.discard(min(neg, key=lambda i: x[i]))
            continue
        # KKT: alguna fijada a 0 que querria subir -> se libera y se repite.
        grad = {i: sum(g[i][j] * x[j] for j in range(n)) - h[i] for i in range(n) if i not in free}
        up = [i for i, v in grad.items() if v < -1e-9]
        if not up:
            break
        free.add(min(up, key=lambda i: grad[i]))
    return {e: max(0.0, x[i]) for e, i in idx.items()}


def _features(coef: dict[str, float]) -> dict[str, float]:
    """Cada luz gasta  fijo + variable * brillo  estando encendida: un LED al
    1 % NO gasta el 1 % (6-nov: de noche la zona pone las luces al 1 % y con
    el modelo proporcional esas muestras no valian para nada)."""
    out = {}
    for e, c in coef.items():
        if c:
            out[e + BASE_SUFFIX] = 1.0 if c > 0 else -1.0
            out[e] = c
    return out


def solve_lights(samples: list) -> dict[str, tuple[float, float]]:
    """(fijo, variable al 100 %) en vatios de cada luz segun las muestras
    [(ts, {luz: brillo con signo}, vatios)]. Solo muestras recientes; las que
    mas se alejan del ajuste (un horno que arranca a la vez...) se quitan y se
    rehace. Luces con menos de MIN_SAMPLES apariciones: sin dato (no se
    inventa)."""
    now = time.time()
    data = [(_features(c), w) for ts, c, w in samples if now - ts <= MAX_AGE_DAYS * 86400 and c]
    data = [(r, w) for r, w in data if r]
    if not data:
        return {}
    ridge = {k: (RIDGE_BASE if k.endswith(BASE_SUFFIX) else RIDGE_LEVEL) for r, _ in data for k in r}
    rows, targets = [r for r, _ in data], [w for _, w in data]
    p = _nnls(rows, targets, ridge)
    resid = [t - sum(p[e] * v for e, v in r.items()) for r, t in zip(rows, targets)]
    if len(resid) >= MIN_SAMPLES:
        med = statistics.median(resid)
        mad = statistics.median(abs(x - med) for x in resid) or max(0.5, abs(statistics.median(targets)) * 0.05)
        keep = [abs(x - med) <= OUTLIER_MAD * mad for x in resid]
        if sum(keep) >= MIN_SAMPLES and not all(keep):
            rows = [r for r, k in zip(rows, keep) if k]
            targets = [t for t, k in zip(targets, keep) if k]
            p = _nnls(rows, targets, ridge)
    out = {}
    for k in p:
        if k.endswith(BASE_SUFFIX):
            continue
        seen = sum(1 for r in rows if r.get(k))
        base, var = p.get(k + BASE_SUFFIX, 0.0), p[k]
        if seen >= MIN_SAMPLES and base + var <= MAX_LIGHT_W:
            out[k] = (round(base, 2), round(var, 2))
    return out


def light_watts(model: tuple[float, float], level: float) -> float:
    """Vatios de una luz encendida al brillo `level` (0..1)."""
    base, var = model
    return base + var * level


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
        self._levels: dict[str, deque] = {}     # luz -> (ts, brillo) recientes
        self._pending: list[dict] = []
        self._last_energy: tuple[float, float] | None = None
        self._solved: dict[str, tuple[float, float]] = solve_lights(self.state["groups"])
        self.power_w: float = 0.0
        self.unlearned: list[str] = []

    # -- observacion ---------------------------------------------------
    def observe(self, now: float, levels: dict[str, float]) -> None:
        """`levels`: brillo actual (0..1, 0 = apagada) de cada luz con dato."""
        for entity_id, level in levels.items():
            prev = self._prev.get(entity_id)
            self._prev[entity_id] = level
            hist = self._levels.setdefault(entity_id, deque())
            if not hist or abs(hist[-1][1] - level) >= 0.01:
                hist.append((now, level))
            while len(hist) > 1 and now - hist[1][0] > LEVEL_HISTORY_S:
                hist.popleft()
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
        # Salto en el momento (6-nov): pocos segundos antes contra pocos segundos
        # despues del cambio -- en ese rato el aire y las baterias casi no se
        # mueven. Ninguna muestra se descarta por ruido ni por su signo (eso
        # sesgaba): el ruido se compensa con MUCHAS muestras y la mediana/ajuste
        # de solve_lights, que ademas quita las que se alejan del resto.
        before = _median_between(points, ts - STEP_BEFORE_S, ts)
        after = _median_between(points, ts + CLUSTER_S + STEP_AFTER_FROM_S, ts + CLUSTER_S + STEP_AFTER_TO_S)
        if before is None or after is None:
            return False
        delta = after - before
        if abs(delta) > MAX_LIGHT_W * len(coef):
            return False
        # Brillo DURANTE la medida, no el del instante del cambio: la zona puede
        # subirlas (refuerzo por presencia) o bajarlas justo despues. Si alguna
        # cambia de brillo dentro de la ventana, el salto no es de un brillo
        # concreto: fuera.
        t_from, t_to = ts + CLUSTER_S, ts + CLUSTER_S + STEP_AFTER_TO_S
        measured = {}
        for e, c in coef.items():
            lv = self._level_during(e, t_from, t_to)
            if lv is False or (c > 0 and lv == 0) or (c < 0 and lv):
                return False
            measured[e] = (lv if lv is not None else c) if c > 0 else c
        coef = measured
        groups = self.state["groups"]
        groups.append([round(ts, 1), {e: round(c, 3) for e, c in coef.items()}, round(delta, 1)])
        del groups[:-MAX_SAMPLES]
        return True

    def _level_during(self, entity_id: str, t0: float, t1: float):
        """Brillo de la luz entre t0 y t1; False si cambio en medio; None si no
        hay historial."""
        hist = self._levels.get(entity_id)
        if not hist:
            return None
        vals = {lv for t, lv in hist if t0 < t <= t1}
        at_start = None
        for t, lv in hist:
            if t <= t0:
                at_start = lv
        if at_start is not None:
            vals.add(at_start)
        if len(vals) > 1:
            return False
        return next(iter(vals), None)

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
            model = self._solved.get(entity_id)
            if model is None:
                unlearned.append(entity_id)
            else:
                total += light_watts(model, level)
        self.power_w, self.unlearned = round(total, 1), unlearned
        self._last_energy = (now, self.power_w)
        return self.power_w

    @property
    def energy_kwh(self) -> float:
        return round(float(self.state.get("energy_wh") or 0.0) / 1000, 4)

    def learned(self) -> dict[str, float]:
        """Vatios al 100 % de cada luz aprendida."""
        return {e: round(light_watts(m, 1.0), 1) for e, m in self._solved.items()}

    def learned_models(self) -> dict[str, tuple[float, float]]:
        """(fijo, variable al 100 %) de cada luz aprendida."""
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


def meter_reader(ws):
    """Lectura del consumo de la casa AHORA a traves del plugin de Shelly
    (6-nov, decision del usuario: todo lo de Shelly lo hace ese plugin; lectura
    directa al segundo, sin el desfase de los sensores de HA). Sin contador en
    el plugin de Shelly, el sensor de consumo de HA."""
    try:
        import device_registry
        meters = device_registry.list_actuators("meter")
        handle = device_registry.resolve(meters[0]["ref"], "meter") if meters else None
    except Exception:
        handle = None
    if handle is not None:
        return handle.read_now, f"contador Shelly {meters[0].get('name')}"
    sensor = home_power_sensor(ws)

    def read_ha() -> float | None:
        try:
            return float((ws.get_state(sensor) or {}).get("state"))
        except (TypeError, ValueError):
            return None
    return read_ha, sensor


def step_deltas(samples: list[tuple[float, float]], steps: list[tuple[float, int]], own: set[str]) -> list[float]:
    """Salto (W, ya con su signo: positivo = lo que gastan las luces) de cada
    encendido/apagado `steps` [(instante, +1/-1)] en la serie del contador."""
    out = []
    for t, sign in steps:
        lo, hi = STEP_OTHER_GUARD_S
        with _events_lock:
            if any(e not in own and t - lo <= te <= t + hi for te, e in _events):
                continue
        before = sorted(w for ts, w in samples if t - STEP_BEFORE_S <= ts < t)
        after = sorted(w for ts, w in samples if t + STEP_AFTER_FROM_S <= ts <= t + STEP_AFTER_TO_S)
        if len(before) < 2 or len(after) < 3:
            continue
        out.append(round((after[len(after) // 2] - before[len(before) // 2]) * sign, 1))
    return out


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
