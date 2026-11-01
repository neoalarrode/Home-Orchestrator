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


def read_shelly_w(host: str, session: requests.Session | None = None) -> float | None:
    """Potencia activa total del Shelly Pro 3EM (positiva = importando)."""
    try:
        r = (session or requests).get(f"http://{host}/rpc/EM.GetStatus?id=0", timeout=METER_TIMEOUT_SECONDS)
        r.raise_for_status()
        return float(r.json()["total_act_power"])
    except Exception:
        return None


def compute_targets(meter_w: float, current: dict[str, float], followers: list[dict],
                    discharge_allowed: bool, contracted_w: float, margin_w: float) -> dict[str, float]:
    """Carga base objetivo de cada bateria.

    `current`: lo que entrega ahora cada una (la ultima carga base mandada).
    `followers`: [{"id", "max_w", "available_wh"}] -- baterias en semiautomatico
    con margen de SOC.
    """
    if not followers:
        return {}
    demand = meter_w + sum(current.get(f["id"], 0.0) for f in followers)
    if discharge_allowed:
        wanted = demand - margin_w
    elif contracted_w > 0:
        # Fuera de descarga planificada: solo lo que pase del contratado.
        wanted = demand - (contracted_w - margin_w)
    else:
        wanted = 0.0
    wanted = max(0.0, min(wanted, sum(f["max_w"] for f in followers)))
    return _distribute(wanted, followers)


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

    def __init__(self, context, send):
        self._context = context
        self._send = send
        self._current: dict[str, float] = {}
        self._last_meter: float | None = None
        self._last_meter_ts = 0.0
        self._last_shadow_log = 0.0
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
            if self._current and any(self._current.values()):
                self._apply({k: 0.0 for k in self._current}, ctx, "seguimiento desactivado")
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

        followers = ctx.get("followers") or []
        targets = compute_targets(
            meter, self._current, followers, bool(ctx.get("discharge_allowed")),
            float(ctx.get("contracted_w") or 0), float(ctx.get("margin_w") or DEFAULT_MARGIN_W),
        )
        # Baterias que han dejado de seguir (cambio de modo, sin SOC...): a 0.
        for bid in list(self._current):
            if bid not in targets:
                targets[bid] = 0.0
        self._apply(targets, ctx, f"contador {meter:.0f} W")

    def _apply(self, targets: dict[str, float], ctx: dict, why: str) -> None:
        mode = ctx.get("mode") or "off"
        deadband = float(ctx.get("deadband_w") or DEFAULT_DEADBAND_W)
        by_id = {b["id"]: b for b in ctx.get("batteries_cfg") or []}
        changes = {bid: w for bid, w in targets.items()
                   if abs(w - self._current.get(bid, 0.0)) >= deadband or (w == 0 and self._current.get(bid, 0.0) > 0)}
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
                self._current[bid] = w
