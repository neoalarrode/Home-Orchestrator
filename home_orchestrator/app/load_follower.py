"""
Seguimiento de carga: Energy hace de CONTADOR de cada bateria STREAM.

Oficialmente un contador (el Shelly Pro 3EM con firmware de EcoFlow) solo se
puede vincular a UN grupo, y un grupo tiene un limite de salida de 1200 W. Con
cada bateria en su propio grupo, sin contador, la unidad queda en modo
semiautomatico: entrega a la casa la CARGA BASE que se le fije (puente BLE
0.5.0, `set_base_load`; medido el 1-nov: aplica en ~2 s). Este modulo lee el
Shelly por RPC local una vez por segundo (en HA solo se actualiza cada ~15 s)
y, en cada cambio, recalcula cuanto tiene que entregar cada bateria:

    demanda = lo que marca el Shelly (importacion) + lo que ya entregan
    objetivo = demanda - margen anti-vertido, repartido por energia disponible,
               con el limite de cada bateria (max_discharge_w, 1200 W)

Solo se descarga cuando el planificador lo permite (ciclo de 30 s, ver
`run_cycle`): en carga o reposo el objetivo es 0, salvo que la casa se acerque
a la potencia contratada (5 kW), en cuyo caso se cubre solo el exceso.

Modos (`general.load_follow_mode`): "off" (nada), "shadow" (calcula y lo
registra, sin mandar nada -- para validar con datos reales) y "on".
"""

from __future__ import annotations

import logging
import threading
import time

import requests

log = logging.getLogger("load_follower")

POLL_SECONDS = 1.0
DEFAULT_MARGIN_W = 50          # importacion que se deja siempre (anti-vertido)
DEFAULT_DEADBAND_W = 25        # cambios por debajo de esto no se mandan
METER_TIMEOUT_SECONDS = 2.0
METER_LOST_SECONDS = 5.0       # sin lectura del contador este tiempo -> todo a 0
SHADOW_LOG_SECONDS = 15.0      # en sombra, un resumen cada tanto (no cada segundo)
# Reparto por EFICIENCIA (decision del usuario, 1-nov): el minimo de unidades
# activas -- un inversor a poca potencia rinde peor y cada unidad mas son mas
# ordenes BLE. Se suma una cuando la demanda pasa de ADD_W por unidad activa y
# se quita cuando cabe en una menos a menos de REMOVE_W cada una (histeresis).
ADD_W_PER_UNIT = 900
REMOVE_W_PER_UNIT = 600
# Rotacion para no desgastar siempre las mismas: entra la de mas SOC, y una
# inactiva que supere a una activa en SWAP_SOC_POINTS se intercambia (como mucho
# una vez cada SWAP_MIN_SECONDS).
SWAP_SOC_POINTS = 10
SWAP_MIN_SECONDS = 600
# Respuesta de una STREAM a un cambio de carga base (traza del 1-nov): se nota
# a los ~2 s y sube a unos 300 W/s por unidad. La demanda se calcula con lo que
# se ESTIMA entregado, no con lo pedido -- dando por entregado lo pedido desde el
# primer instante, mientras subian las rampas el contador seguia marcando
# importacion, se pedia todavia mas y al acabar se vertian 824 W.
RESPONSE_DELAY_S = 2.0
RAMP_UP_W_PER_S = 300.0
# Subida maxima por lectura y unidad (las bajadas son inmediatas): no perseguir
# del todo un pico corto que al desaparecer dejaria vertiendo.
MAX_STEP_UP_W = 300.0
# Cada cuanto se comprueba que un equipo que descarga sigue con la carga
# desactivada y la descarga activa. BUG REAL (1-nov): una orden de carga del plan
# que iba por detras en el puente BLE llego DESPUES de preparar la prueba y dejo
# los dos grupos cargando, con la carga base puesta pero sin entregar nada.
ENSURE_TASKS_SECONDS = 10.0
# Cada orden se verifica leyendo el equipo VERIFY_AFTER_S despues (el equipo
# tarda ~2 s en aplicarla: leer al instante daria el valor anterior).
VERIFY_AFTER_S = 3.0


def read_shelly_w(host: str, session: requests.Session | None = None) -> float | None:
    """Potencia activa total del Shelly Pro 3EM (positiva = importando)."""
    try:
        r = (session or requests).get(f"http://{host}/rpc/EM.GetStatus?id=0", timeout=METER_TIMEOUT_SECONDS)
        r.raise_for_status()
        return float(r.json()["total_act_power"])
    except Exception:
        return None


def wanted_total(meter_w: float, current: dict[str, float], followers: list[dict],
                 discharge_allowed: bool, contracted_w: float, margin_w: float,
                 other_charging_w: float = 0.0, others: list[dict] | None = None) -> float:
    """Potencia total que tienen que entregar las baterias ahora.

    `other_charging_w`: lo que estan cargando de red las baterias que NO siguen
    (el Shelly lo ve como consumo): se descuenta para no descargar unas para
    cargar otras, perdiendo en los dos sentidos."""
    # Lo que entregan ahora los GRUPOS con contador propio (siguen al Shelly
    # por su cuenta): tambien es parte del consumo de la casa.
    others_out = sum(max(0.0, o.get("output_w") or 0.0) for o in (others or []))
    demand = meter_w + sum(current.get(f["id"], 0.0) for f in followers) + others_out - max(0.0, other_charging_w)
    if discharge_allowed:
        wanted = demand - margin_w
    elif contracted_w > 0:
        # Fuera de descarga planificada: solo lo que pase del contratado.
        wanted = demand - (contracted_w - margin_w)
    else:
        wanted = 0.0
    cap = sum(f["max_w"] for f in followers + list(others or []) if f["available_wh"] > 0)
    return max(0.0, min(wanted, cap))


def compute_targets(meter_w: float, current: dict[str, float], followers: list[dict],
                    discharge_allowed: bool, contracted_w: float, margin_w: float,
                    other_charging_w: float = 0.0, others: list[dict] | None = None,
                    active: list[str] | None = None) -> dict[str, float]:
    """Carga base objetivo de cada bateria (todas, a 0 las inactivas).

    `current`: lo que entrega ahora cada una (la ultima carga base mandada).
    `followers`: [{"id", "max_w", "available_wh", "soc"}] -- baterias en
    semiautomatico. `active`: las que se usan (ver `choose_active`); sin el,
    todas las que tengan energia.
    """
    if not followers:
        return {}
    wanted = wanted_total(meter_w, current, followers, discharge_allowed, contracted_w, margin_w, other_charging_w, others)
    # Los grupos con contador cuentan como una unidad mas en el reparto (su
    # tope y su energia), pero no se les manda nada: lo que les toque lo cubren
    # solos siguiendo al Shelly, porque las sueltas ya entregan su parte.
    pool = followers + list(others or [])
    use = [f for f in pool if (active is None or f["id"] in active) and f["available_wh"] > 0]
    shares = _distribute(wanted, use)
    return {f["id"]: shares.get(f["id"], 0.0) for f in followers}


def choose_active(wanted_w: float, followers: list[dict], active: list[str]) -> list[str]:
    """Que unidades descargan, por eficiencia: las minimas necesarias, con
    histeresis, entrando las de mas SOC. `active` es la eleccion anterior."""
    usable = [f for f in followers if f["available_wh"] > 0 and f["max_w"] > 0]
    by_id = {f["id"]: f for f in usable}
    active = [a for a in active if a in by_id]          # las que se vaciaron o salieron, fuera
    if wanted_w <= 0:
        return []
    k = max(1, len(active))
    while k < len(usable) and wanted_w > k * ADD_W_PER_UNIT:
        k += 1
    while k > 1 and wanted_w < (k - 1) * REMOVE_W_PER_UNIT:
        k -= 1
    # Ademas, nunca menos de las que hacen falta por su limite fisico.
    ranked = sorted(usable, key=lambda f: f.get("soc") or 0, reverse=True)
    while k < len(usable) and wanted_w > sum(f["max_w"] for f in ranked[:k]):
        k += 1
    keep = sorted(active, key=lambda a: by_id[a].get("soc") or 0, reverse=True)[:k]
    for f in ranked:
        if len(keep) >= k:
            break
        if f["id"] not in keep:
            keep.append(f["id"])
    return keep


def maybe_swap(active: list[str], followers: list[dict]) -> list[str]:
    """Una inactiva con bastante mas SOC sustituye a la activa de menos SOC."""
    by_id = {f["id"]: f for f in followers if f["available_wh"] > 0}
    act = [a for a in active if a in by_id]
    idle = [f for f in by_id.values() if f["id"] not in act]
    if not act or not idle:
        return act
    low = min(act, key=lambda a: by_id[a].get("soc") or 0)
    best = max(idle, key=lambda f: f.get("soc") or 0)
    if (best.get("soc") or 0) - (by_id[low].get("soc") or 0) >= SWAP_SOC_POINTS:
        return [a for a in act if a != low] + [best["id"]]
    return act


def _distribute(total_w: float, followers: list[dict]) -> dict[str, float]:
    """Reparto proporcional a la energia disponible, sin pasar el limite de cada
    una; lo que una no pueda dar se reparte entre las demas."""
    result = {f["id"]: 0.0 for f in followers}
    pending = [f for f in followers if f["max_w"] > 0 and f["available_wh"] > 0]
    remaining = total_w
    while remaining > 1e-6 and pending:
        weight = sum(f["available_wh"] for f in pending)
        nxt = []
        given = 0.0
        for f in pending:
            take = min(remaining * f["available_wh"] / weight, f["max_w"] - result[f["id"]])
            result[f["id"]] += take
            given += take
            if f["max_w"] - result[f["id"]] > 1e-6:
                nxt.append(f)
        remaining -= given
        if given <= 1e-6:
            break
        pending = nxt
    return {k: round(v) for k, v in result.items()}


class LoadFollower:
    """Hilo del seguimiento. `context()` lo da main.py: config, SOC y permiso
    del planificador; `send(battery_cfg, watts)` manda la carga base."""

    def __init__(self, context, send, release=None, ensure=None, verify=None):
        self._context = context
        self._send = send
        # `release(battery_cfg)`: devolver la bateria al planificador cuando deja
        # de seguir (fin de la prueba, cambio de modo...).
        self._release = release
        # `ensure(battery_cfg)`: comprobar/rehacer sus tareas (carga off, descarga on).
        self._ensure = ensure
        self._last_ensure: dict[str, float] = {}
        # `verify(battery_cfg, watts) -> bool`: comprobar que la orden se aplico
        # (y rehacerla si no). Pendientes: {id: (cuando, vatios)}.
        self._verify = verify
        self._verify_due: dict[str, tuple[float, float]] = {}
        self._followed: set[str] = set()
        self._last_log = 0.0
        self._current: dict[str, float] = {}
        self._last_meter: float | None = None
        self._last_meter_ts = 0.0
        self._last_shadow_log = 0.0
        self._active: list[str] = []
        self._last_swap = 0.0
        # Estimacion de lo entregado de verdad por cada equipo, y el historial de
        # ordenes [(ts, vatios)] con el que se calcula (ver _advance_estimates).
        self._estimate: dict[str, float] = {}
        self._orders: dict[str, list[tuple[float, float]]] = {}
        self._last_advance = None
        self._stop = threading.Event()
        self.status: dict = {"mode": "off"}

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        session = requests.Session()
        while not self._stop.wait(POLL_SECONDS):
            try:
                self._tick(session)
            except Exception:
                log.exception("Seguimiento de carga: fallo en el ciclo")

    def _tick(self, session) -> None:
        ctx = self._context()
        mode = ctx.get("mode") or "off"
        host = ctx.get("meter_host")
        if mode == "off" or not host:
            self._release_missing(set(), ctx)
            self.status = {"mode": mode}
            return
        now = time.monotonic()
        meter = read_shelly_w(host, session)
        if meter is None:
            if now - self._last_meter_ts > METER_LOST_SECONDS and any(self._current.values()):
                log.warning("Seguimiento de carga: sin lectura del contador %s -- carga base a 0", host)
                self._apply({k: 0.0 for k in self._current}, ctx, "contador sin respuesta")
            return
        self._last_meter_ts = now
        if meter == self._last_meter:
            return  # solo se recalcula cuando el contador cambia
        self._last_meter = meter

        self._advance_estimates(now)
        followers = ctx.get("followers") or []
        # Fuera del modo real nadie se queda con una carga base puesta. BUG REAL
        # (1-nov): al vencer la prueba el modo paso a sombra y la Ultra X se
        # quedo con 350 W de carga base -- la proxima descarga del plan los
        # habria entregado fijos, sin nadie siguiendo a la casa.
        self._release_missing({f["id"] for f in followers} if mode == "on" else set(), ctx)
        others = ctx.get("others") or []
        delivered = {k: self._estimate.get(k, v) for k, v in self._current.items()}
        args = (meter, delivered, followers, bool(ctx.get("discharge_allowed")),
                float(ctx.get("contracted_w") or 0), float(ctx.get("margin_w") or DEFAULT_MARGIN_W),
                float(ctx.get("other_charging_w") or 0), others)
        wanted = wanted_total(*args)
        pool = followers + others
        self._active = choose_active(wanted, pool, self._active)
        if self._active and now - self._last_swap >= SWAP_MIN_SECONDS:
            swapped = maybe_swap(self._active, pool)
            if set(swapped) != set(self._active):
                self._last_swap = now
                self._active = swapped
        targets = compute_targets(*args, active=self._active)
        # Subida limitada respecto a lo que ya entrega (bajadas inmediatas).
        targets = {k: min(v, delivered.get(k, 0.0) + MAX_STEP_UP_W) if v > delivered.get(k, 0.0) else v
                   for k, v in targets.items()}
        if ctx.get("trace"):
            # Diagnostico (general.load_follow_trace): cada lectura con lo pedido.
            log.info("[SEGUIMIENTO TRAZA] contador %.0f W, entregando %s, otros %s, objetivo %s", meter,
                     {k: round(v) for k, v in delivered.items()},
                     [round(o.get("output_w") or 0) for o in others], {k: round(v) for k, v in targets.items()})
        # Baterias que han dejado de seguir (cambio de modo, sin SOC...): a 0.
        for bid in list(self._current):
            if bid not in targets:
                targets[bid] = 0.0
        self._apply(targets, ctx, f"contador {meter:.0f} W")
        if mode == "on" and self._verify is not None:
            by_id = {b["id"]: b for b in ctx.get("batteries_cfg") or []}
            for bid, (due, w) in list(self._verify_due.items()):
                if now >= due and bid in by_id:
                    del self._verify_due[bid]
                    current = self._current.get(bid)
                    if current is None:
                        continue
                    # La carga base solo se compara si la orden ya tiene VERIFY_AFTER_S
                    # (si es mas nueva el equipo aun no ha podido aplicarla); las
                    # tareas, siempre.
                    last_order = (self._orders.get(bid) or [(0.0, current)])[-1][0]
                    if not self._verify(by_id[bid], current, now - last_order >= VERIFY_AFTER_S):
                        # No aplicada: que la proxima lectura la vuelva a mandar.
                        self._current.pop(bid, None)
        if mode == "on" and self._ensure is not None:
            by_id = {b["id"]: b for b in ctx.get("batteries_cfg") or []}
            for bid, w in self._current.items():
                if w > 0 and bid in by_id and now - self._last_ensure.get(bid, 0.0) >= ENSURE_TASKS_SECONDS:
                    self._last_ensure[bid] = now
                    self._ensure(by_id[bid])

    def _apply(self, targets: dict[str, float], ctx: dict, why: str) -> None:
        mode = ctx.get("mode") or "off"
        deadband = float(ctx.get("deadband_w") or DEFAULT_DEADBAND_W)
        by_id = {b["id"]: b for b in ctx.get("batteries_cfg") or []}
        # Un equipo que aun no hemos mandado nunca recibe su objetivo aunque sea
        # 0: puede traer una carga base de antes (el grupo de tres tenia 510 W)
        # que entregaria fija en cuanto se le active la descarga.
        changes = {bid: w for bid, w in targets.items()
                   if bid not in self._current or abs(w - self._current[bid]) >= deadband
                   or (w == 0 and self._current[bid] > 0)}
        self.status = {"mode": mode, "meter_w": self._last_meter, "targets": dict(targets), "why": why,
                       "discharge_allowed": bool(ctx.get("discharge_allowed"))}
        if mode == "shadow":
            # En sombra se simula que la bateria aplica lo pedido, para que el
            # siguiente calculo parta de ahi como lo haria de verdad.
            self._current.update(changes)
            now = time.monotonic()
            if changes and now - self._last_shadow_log >= SHADOW_LOG_SECONDS:
                self._last_shadow_log = now
                names = {bid: (by_id.get(bid) or {}).get("name", bid) for bid in targets}
                log.info("[SEGUIMIENTO SOMBRA] %s -> %s", why,
                         ", ".join(f"{names[b]} {w:.0f} W" for b, w in targets.items()))
            return
        for bid, w in changes.items():
            b = by_id.get(bid)
            if b is None:
                continue
            if self._send(b, w):
                self._orders.setdefault(bid, []).append((time.monotonic(), w))
                # No se reprograma una verificacion pendiente: mandando cada
                # segundo no llegaria nunca.
                self._verify_due.setdefault(bid, (time.monotonic() + VERIFY_AFTER_S, w))
                self._current[bid] = w
                self._followed.add(bid)
            else:
                log.warning("[SEGUIMIENTO] %s: no se pudo aplicar la carga base de %.0f W -- se reintenta en la proxima lectura",
                            b.get("name", bid), w)
        now = time.monotonic()
        if changes and now - self._last_log >= SHADOW_LOG_SECONDS:
            self._last_log = now
            log.info("[SEGUIMIENTO] %s -> %s", why, ", ".join(
                f"{(by_id.get(b) or {}).get('name', b)} {w:.0f} W" for b, w in targets.items()))

    def _advance_estimates(self, now: float) -> None:
        """Lo entregado de verdad va hacia la orden que habia hace
        RESPONSE_DELAY_S (la que el equipo ya esta aplicando): sube a
        RAMP_UP_W_PER_S y baja de golpe. BUG REAL de la primera version: cada
        orden nueva reiniciaba el retraso, y mandando cada segundo la estimacion
        se quedaba congelada -- el seguimiento no veia el efecto de lo que
        mandaba y oscilaba."""
        dt = 0.0 if self._last_advance is None else max(0.0, now - self._last_advance)
        self._last_advance = now
        for bid, history in self._orders.items():
            applied = None
            for ts, w in history:
                if ts <= now - RESPONSE_DELAY_S:
                    applied = w
            # Solo hace falta la ultima orden ya aplicada y las posteriores.
            while len(history) > 1 and history[1][0] <= now - RESPONSE_DELAY_S:
                history.pop(0)
            if applied is None:
                continue
            est = self._estimate.get(bid, 0.0)
            self._estimate[bid] = applied if applied <= est else min(applied, est + RAMP_UP_W_PER_S * dt)

    def _release_missing(self, still: set[str], ctx: dict) -> None:
        """Las baterias que seguian y ya no: a 0 y devueltas al planificador."""
        by_id = {b["id"]: b for b in ctx.get("batteries_cfg") or []}
        for bid in [x for x in self._followed if x not in still]:
            b = by_id.get(bid)
            done = b is None or self._release is None or self._release(b)
            if done:
                self._followed.discard(bid)
                self._current.pop(bid, None)
                self._estimate.pop(bid, None)
                self._orders.pop(bid, None)
                self._active = [a for a in self._active if a != bid]
                log.info("[SEGUIMIENTO] %s deja de seguir: carga base a 0 y vuelve al planificador",
                         (b or {}).get("name", bid))
