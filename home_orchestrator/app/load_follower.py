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
# Proporcional a la potencia maxima REAL de las unidades (la que informa cada
# equipo, `max_w`), no un numero fijo: con 1200 W salen los 900/600 de antes.
ADD_FRACTION_OF_MAX = 0.75
REMOVE_FRACTION_OF_MAX = 0.5
# Rotacion (como mucho una vez cada SWAP_MIN_SECONDS): ver SWAP_SCORE_WH.
# Orden de entrada a descargar (6-nov, decision del usuario): primero las que
# mas energia tienen y menos gastan sus propios enchufes -- la energia que les
# quedaria para la casa tras alimentar lo enchufado hasta el proximo valle
# (`available_wh - outlet_drain_wh`). Es solo un ORDEN: no se reserva nada.
# Se cambia una activa por una parada si esta tiene al menos SWAP_SCORE_WH mas.
SWAP_SCORE_WH = 200.0
SWAP_MIN_SECONDS = 600
# Respuesta de una STREAM a un cambio de carga base (traza del 1-nov): se nota
# a los ~2 s y sube a unos 300 W/s por unidad. La demanda se calcula con lo que
# se ESTIMA entregado, no con lo pedido -- dando por entregado lo pedido desde el
# primer instante, mientras subian las rampas el contador seguia marcando
# importacion, se pedia todavia mas y al acabar se vertian 824 W.
# Valores de PARTIDA: cada bateria aprende los suyos (ver ResponseLearner).
RESPONSE_DELAY_S = 2.0
RAMP_UP_W_PER_S = 300.0
# Subida maxima por lectura y unidad (las bajadas son inmediatas): no perseguir
# del todo un pico corto que al desaparecer dejaria vertiendo.
STEP_UP_HEADROOM = 1.5
# (El salto maximo por lectura ya no es fijo: es lo que esa bateria puede
# moverse de verdad entre dos lecturas, su ritmo aprendido x POLL_SECONDS --
# pedir mas solo acumula demanda a ciegas y se pasa si el pico desaparece.)
# CARGA CON EXCEDENTE (placas en alterna, decision del usuario 3-nov): si la
# casa vierte, se sube el limite de carga de las baterias hasta absorberlo; si
# importa mientras cargan con sol, se baja. Lo que cargan por alterna pasa por
# el Shelly, asi que la casa real es contador + descarga - carga (ver
# `house_net_w`). Respuesta medida el 3-nov (Despacho, limite 300 -> 600 -> 278 W):
# al subir se nota en la red a ~1 s y sube ~100 W/s; al bajar tarda ~2.5 s y cae
# de golpe; carga ~4 % por encima de lo pedido (el bucle lo corrige).
SOLAR_CHARGE_START_W = 60      # vertido que hace falta para empezar a cargar
SOLAR_CHARGE_MIN_W = 40        # por debajo de esto una bateria no se pone a cargar
CHARGE_RESPONSE_DELAY_S = 2.0
CHARGE_RAMP_UP_W_PER_S = 100.0
# Cada cuanto se comprueba que un equipo que descarga sigue con la carga
# desactivada y la descarga activa. BUG REAL (1-nov): una orden de carga del plan
# que iba por detras en el puente BLE llego DESPUES de preparar la prueba y dejo
# los dos grupos cargando, con la carga base puesta pero sin entregar nada.
ENSURE_TASKS_SECONDS = 10.0
# Cada orden se verifica leyendo el equipo VERIFY_AFTER_S despues (el equipo
# tarda ~2 s en aplicarla: leer al instante daria el valor anterior).
VERIFY_AFTER_S = 3.0
# Liberar un equipo que no responde: reintentos en su hilo, no en cada lectura.
RELEASE_ATTEMPTS = 5
RELEASE_RETRY_S = 10.0
# Llamadas en hilos por equipo (False en tests: en linea).
ASYNC_CALLS = True


def _num_or(value, default: float) -> float:
    """`value` como numero, o `default` si no esta puesto. Con `value or
    default`, un margen de 0 W (apurar al maximo) se convertia en el de fabrica."""
    try:
        return float(value) if value is not None and value != "" else float(default)
    except (TypeError, ValueError):
        return float(default)


class ResponseLearner:
    """Cuanto tarda CADA bateria en empezar a notar una orden y a que ritmo
    llega, medido en ella misma (decision del usuario, 3-nov: que Energy lo
    calcule solo, no valores escritos a mano). Cuatro casos por bateria: subir
    y bajar la carga base (descarga) y subir y bajar el limite de carga.

    Se abre una medida con cada orden que cambia al menos LEARN_MIN_STEP_W; se
    cierra cuando la potencia medida del equipo (su puerto de red, por BLE) ha
    recorrido el LEARN_DONE_FRACTION del salto: el retraso es hasta que se movio
    un LEARN_START_FRACTION, y el ritmo el del tramo entre los dos. Cada medida
    se mezcla con lo aprendido (LEARN_ALPHA), dentro de limites fisicos."""

    LEARN_MIN_STEP_W = 150.0
    LEARN_START_FRACTION = 0.15
    LEARN_MOVED_W = 40.0
    LEARN_SMALL_RUN_S = 3.0
    LEARN_BEHIND_W = 80.0
    LEARN_KEEPUP_FRACTION = 0.8
    LEARN_EXPLORE_FACTOR = 1.25
    LEARN_DONE_FRACTION = 0.85
    LEARN_TIMEOUT_S = 20.0
    LEARN_ALPHA = 0.3
    DELAY_RANGE_S = (0.3, 10.0)
    RAMP_RANGE_W_PER_S = (20.0, 3000.0)
    DEFAULTS = {
        "base_up": {"delay": RESPONSE_DELAY_S, "ramp": RAMP_UP_W_PER_S},
        "base_down": {"delay": RESPONSE_DELAY_S, "ramp": 3000.0},
        "charge_up": {"delay": CHARGE_RESPONSE_DELAY_S, "ramp": CHARGE_RAMP_UP_W_PER_S},
        "charge_down": {"delay": CHARGE_RESPONSE_DELAY_S + 0.5, "ramp": 3000.0},
    }

    def __init__(self, store=None):
        # `store`: (cargar() -> dict, guardar(dict)) para no perderlo al reiniciar.
        self._store = store
        loaded = store[0]() if store else None
        self.params: dict[str, dict[str, dict[str, float]]] = loaded if isinstance(loaded, dict) else {}
        self._probes: dict[tuple[str, str], dict] = {}
        self.samples: dict[str, dict[str, int]] = {}

    def get(self, bid: str, case: str) -> dict[str, float]:
        return (self.params.get(bid) or {}).get(case) or dict(self.DEFAULTS[case])

    def order(self, bid: str, kind: str, t: float, from_w: float, to_w: float) -> None:
        """Una orden nueva a `bid` (`kind` = "base" o "charge"). Las ordenes
        seguidas en el mismo sentido son UN salto (el seguimiento sube poco a
        poco): la medida empieza en la primera y su destino es la ultima."""
        step = to_w - from_w
        if abs(step) < 1:
            return
        direction = "up" if step > 0 else "down"
        other = "down" if direction == "up" else "up"
        # Una orden en sentido contrario cierra el tramo del otro sentido.
        self._probes.pop((bid, f"{kind}_{other}"), None)
        key = (bid, f"{kind}_{direction}")
        probe = self._probes.get(key)
        # Un tramo que aun no es un salto (orden pequeña) y ya no es reciente se
        # reinicia: si no, un salto grande posterior heredaria un inicio antiguo
        # y el retraso saldria inflado.
        if probe is not None and abs(probe["to"] - probe["from"]) < self.LEARN_MIN_STEP_W \
                and t - probe["t0"] > self.LEARN_SMALL_RUN_S:
            probe = None
        if probe is None:
            self._probes[key] = {"t0": t, "from": from_w, "to": to_w, "moved": None}
        else:
            probe["to"] = to_w

    def observe(self, bid: str, kind: str, t: float, measured_w: float | None) -> None:
        if measured_w is None:
            return
        for direction in ("up", "down"):
            case = f"{kind}_{direction}"
            probe = self._probes.get((bid, case))
            if not probe:
                continue
            if t - probe["t0"] > self.LEARN_TIMEOUT_S:
                self._probes.pop((bid, case), None)
                continue
            span = probe["to"] - probe["from"]
            if abs(span) < self.LEARN_MIN_STEP_W:
                continue                  # aun no es un salto que merezca medirse
            moved_w = (measured_w - probe["from"]) * (1 if span > 0 else -1)
            # Retraso: hasta que se mueve de verdad (unos vatios), no hasta un %
            # del salto, que va creciendo mientras el seguimiento sigue pidiendo.
            if probe["moved"] is None and moved_w >= min(self.LEARN_MOVED_W, abs(span) * self.LEARN_START_FRACTION):
                probe["moved"], probe["moved_w"] = t, moved_w
            # Ritmo: la MAYOR velocidad vista en el salto (no la media, que con
            # ordenes que avanzan poco a poco seria la de las ordenes, no la
            # del equipo). Una caida de golpe entre dos lecturas cuenta entera.
            # Solo cuenta mientras el equipo va claramente por DETRAS de lo pedido:
            # si no, lo que se mide es el ritmo de las ordenes (el seguimiento
            # frena al acercarse) y, como el tope de subida sale de lo aprendido,
            # el error se reforzaria solo hasta dejarlo cada vez mas lento.
            last = probe.get("last")
            behind = abs(span) - moved_w
            if last is not None and t > last[0]:
                slope = (moved_w - last[1]) / (t - last[0])
                probe["max_slope_any"] = max(probe.get("max_slope_any", 0.0), slope)
                if last[2] >= self.LEARN_BEHIND_W:
                    probe["max_slope"] = max(probe.get("max_slope", 0.0), slope)
            probe["last"] = (t, moved_w, behind)
            if probe["moved"] is not None and moved_w >= abs(span) * self.LEARN_DONE_FRACTION:
                delay = probe["moved"] - probe["t0"]
                ramp = probe.get("max_slope")
                cur_ramp = self.get(bid, case)["ramp"]
                # Si recorrio el salto entre dos lecturas no hay pendiente que medir:
                # vale su media desde la orden.
                seen = max(probe.get("max_slope_any", 0.0), moved_w / max(t - probe["t0"], 1.0))
                if ramp is None and seen >= self.LEARN_KEEPUP_FRACTION * cur_ramp:
                    # Siguio todo lo pedido sin quedarse atras: puede ir mas
                    # deprisa. Se prueba un poco mas en el siguiente salto.
                    ramp = cur_ramp * self.LEARN_EXPLORE_FACTOR
                self._learn(bid, case, delay, ramp)
                self._probes.pop((bid, case), None)

    def _learn(self, bid: str, case: str, delay: float, ramp: float | None) -> None:
        """`ramp` None = en este salto no se vio el limite del equipo: solo se
        aprende el retraso."""
        lo, hi = self.DELAY_RANGE_S
        delay = min(hi, max(lo, delay))
        cur = self.get(bid, case)
        if ramp is None:
            ramp = cur["ramp"]
        lo, hi = self.RAMP_RANGE_W_PER_S
        ramp = min(hi, max(lo, ramp))
        n = self.samples.setdefault(bid, {}).get(case, 0)
        a = 1.0 if (bid not in self.params or case not in self.params[bid]) else self.LEARN_ALPHA
        new = {"delay": round(cur["delay"] + a * (delay - cur["delay"]), 2),
               "ramp": round(cur["ramp"] + a * (ramp - cur["ramp"]), 1)}
        self.params.setdefault(bid, {})[case] = new
        self.samples[bid][case] = n + 1
        log.info("[SEGUIMIENTO] respuesta aprendida de %s (%s): retraso %.1f s, ritmo %.0f W/s",
                 bid, case, new["delay"], new["ramp"])
        if self._store:
            try:
                self._store[1](self.params)
            except Exception:
                log.exception("Seguimiento: fallo guardando la respuesta aprendida")


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
                 other_charging_w: float = 0.0, others: list[dict] | None = None,
                 own_charging_w: float = 0.0) -> float:
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
        # Fuera de descarga planificada: solo lo que la CASA (sin la carga de
        # las propias baterias, que ya recorta el plan con su tope de potencia
        # contratada) pase del contratado. Contandola, se descargaba una bateria
        # para dejar cargar a otra y se peleaba con el plan, que volvia a
        # activar la carga en cada ciclo.
        wanted = demand - max(0.0, own_charging_w) - (contracted_w - margin_w)
    else:
        wanted = 0.0
    cap = sum(f["max_w"] for f in followers + list(others or []) if f["available_wh"] > 0)
    return max(0.0, min(wanted, cap))


def compute_targets(meter_w: float, current: dict[str, float], followers: list[dict],
                    discharge_allowed: bool, contracted_w: float, margin_w: float,
                    other_charging_w: float = 0.0, others: list[dict] | None = None,
                    own_charging_w: float = 0.0, active: list[str] | None = None) -> dict[str, float]:
    """Carga base objetivo de cada bateria (todas, a 0 las inactivas).

    `current`: lo que entrega ahora cada una (la ultima carga base mandada).
    `followers`: [{"id", "max_w", "available_wh", "soc"}] -- baterias en
    semiautomatico. `active`: las que se usan (ver `choose_active`); sin el,
    todas las que tengan energia.
    """
    if not followers:
        return {}
    wanted = wanted_total(meter_w, current, followers, discharge_allowed, contracted_w, margin_w,
                          other_charging_w, others, own_charging_w)
    # Los grupos con contador cuentan como una unidad mas en el reparto (su
    # tope y su energia), pero no se les manda nada: lo que les toque lo cubren
    # solos siguiendo al Shelly, porque las sueltas ya entregan su parte.
    pool = followers + list(others or [])
    use = [f for f in pool if (active is None or f["id"] in active) and f["available_wh"] > 0]
    shares = _distribute(wanted, use)
    return {f["id"]: shares.get(f["id"], 0.0) for f in followers}


def house_net_w(meter_w: float, delivered: dict[str, float], charging: dict[str, float]) -> float:
    """Lo que pide la casa sin contar las baterias que seguimos: el contador
    mas lo que entregan menos lo que cargan por alterna (pasa por el Shelly).
    Negativo = sobra (placas en alterna vertiendo)."""
    return meter_w + sum(delivered.values()) - sum(charging.values())


def solar_charge_targets(net_w: float, followers: list[dict], charging_now: bool) -> dict[str, float]:
    """Limite de carga de cada seguidor para absorber el excedente `-net_w`.
    Vacio si no hay que cargar (sin excedente suficiente para empezar, o se
    acabo el que habia). Reparto por el hueco que le queda a cada una."""
    surplus = -net_w
    if surplus <= 0 or (not charging_now and surplus < SOLAR_CHARGE_START_W):
        return {}
    pool = [{"id": f["id"], "max_w": float(f.get("max_charge_w") or 0), "available_wh": float(f.get("headroom_wh") or 0)}
            for f in followers if (f.get("headroom_wh") or 0) > 0 and (f.get("max_charge_w") or 0) > 0]
    if not pool:
        return {}
    shares = _distribute(surplus, pool)
    # Una bateria no se pone a cargar por unos pocos vatios: se le pasan a otra.
    small = [k for k, v in shares.items() if 0 < v < SOLAR_CHARGE_MIN_W]
    if small and len(small) < len(shares):
        keep = [p for p in pool if p["id"] not in small]
        shares = {**{k: 0.0 for k in small}, **_distribute(surplus, keep)}
    return {k: float(v) for k, v in shares.items()}


def _score_wh(f: dict) -> float:
    """Energia que le quedaria a la unidad para la casa tras sus enchufes."""
    return float(f.get("available_wh") or 0.0) - float(f.get("outlet_drain_wh") or 0.0)


def _priority(f: dict) -> float:
    return -_score_wh(f)


def choose_active(wanted_w: float, followers: list[dict], active: list[str]) -> list[str]:
    """Que unidades descargan, por eficiencia: las minimas necesarias, con
    histeresis, entrando antes las de mas energia y menos consumo enchufado
    (ver _score_wh). `active` es la eleccion anterior."""
    usable = [f for f in followers if f["available_wh"] > 0 and f["max_w"] > 0]
    by_id = {f["id"]: f for f in usable}
    active = [a for a in active if a in by_id]          # las que se vaciaron o salieron, fuera
    if wanted_w <= 0:
        return []
    unit_w = sum(f["max_w"] for f in usable) / len(usable) if usable else 0.0
    add_w, remove_w = unit_w * ADD_FRACTION_OF_MAX, unit_w * REMOVE_FRACTION_OF_MAX
    k = max(1, len(active))
    while k < len(usable) and wanted_w > k * add_w:
        k += 1
    while k > 1 and wanted_w < (k - 1) * remove_w:
        k -= 1
    # Ademas, nunca menos de las que hacen falta por su limite fisico.
    ranked = sorted(usable, key=_priority)
    while k < len(usable) and wanted_w > sum(f["max_w"] for f in ranked[:k]):
        k += 1
    keep = sorted(active, key=lambda a: _priority(by_id[a]))[:k]
    for f in ranked:
        if len(keep) >= k:
            break
        if f["id"] not in keep:
            keep.append(f["id"])
    return keep


def maybe_swap(active: list[str], followers: list[dict]) -> list[str]:
    """Una parada a la que le quedaria bastante mas energia para la casa
    (SWAP_SCORE_WH) sustituye a la activa a la que menos le quedaria."""
    by_id = {f["id"]: f for f in followers if f["available_wh"] > 0}
    act = [a for a in active if a in by_id]
    idle = [f for f in by_id.values() if f["id"] not in act]
    if not act or not idle:
        return act
    worst = min(act, key=lambda a: _score_wh(by_id[a]))
    best = max(idle, key=_score_wh)
    if _score_wh(best) - _score_wh(by_id[worst]) >= SWAP_SCORE_WH:
        return [a for a in act if a != worst] + [best["id"]]
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


class _Worker:
    """Ejecuta en su propio hilo las llamadas a UN equipo (BLE: pueden tardar
    hasta 40 s si no responde), para que el bucle del contador no espere nunca.
    Las cargas base se agrupan: si llega una nueva antes de mandar la anterior,
    solo se manda la ultima. El resto de trabajos (preparar, verificar,
    liberar) van en orden."""

    def __init__(self, name: str):
        self._cv = threading.Condition()
        self._base: tuple | None = None
        self._charge: tuple | None = None
        self._jobs: list = []
        threading.Thread(target=self._run, name=f"seguimiento-{name}", daemon=True).start()

    def base(self, fn) -> None:
        with self._cv:
            self._base = fn
            self._cv.notify()

    def charge(self, fn) -> None:
        """Limite de carga: mismo criterio que `base`, en su propio hueco."""
        with self._cv:
            self._charge = fn
            self._cv.notify()

    def job(self, fn) -> None:
        with self._cv:
            self._jobs.append(fn)
            self._cv.notify()

    def cancel_base(self) -> None:
        with self._cv:
            self._base = None

    def idle(self) -> bool:
        with self._cv:
            return self._base is None and self._charge is None and not self._jobs

    def _run(self) -> None:
        while True:
            with self._cv:
                while self._base is None and self._charge is None and not self._jobs:
                    self._cv.wait()
                if self._jobs:
                    fn = self._jobs.pop(0)
                elif self._charge is not None:
                    fn, self._charge = self._charge, None
                else:
                    fn, self._base = self._base, None
            try:
                fn()
            except Exception:
                log.exception("Seguimiento de carga: fallo en una llamada al equipo")


class LoadFollower:
    """Hilo del seguimiento. `context()` lo da main.py: config, SOC y permiso
    del planificador; `send(battery_cfg, watts)` manda la carga base."""

    def __init__(self, context, send, release=None, ensure=None, verify=None, charge=None, learner=None):
        self._context = context
        self._send = send
        # `charge(battery_cfg, watts)`: limite de carga (0 = dejar de cargar).
        self._charge = charge
        self._charge_current: dict[str, float] = {}
        self._charge_estimate: dict[str, float] = {}
        self._charge_orders: dict[str, list[tuple[float, float]]] = {}
        self._last_charge_log = 0.0
        self._surplus_since: float | None = None
        self._learner = learner if learner is not None else ResponseLearner()
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
        # Un hilo por equipo para sus llamadas (ver _Worker). `asynchronous=False`
        # (tests) las ejecuta en linea.
        self.asynchronous = ASYNC_CALLS
        self._workers: dict[str, _Worker] = {}
        self._failed: set[str] = set()      # ordenes que el equipo no confirmo
        self._lock = threading.Lock()
        self._warned_direct_meter = False

    def stop(self) -> None:
        self._stop.set()

    def meter_now_w(self) -> float | None:
        """Ultima lectura directa del contador (W, positivo importando) si es de
        los ultimos METER_LOST_SECONDS; None si no se esta leyendo."""
        if self._last_meter is None or time.monotonic() - self._last_meter_ts > METER_LOST_SECONDS:
            return None
        return self._last_meter

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
        reader = ctx.get("meter_read")
        if reader is not None:
            meter = reader()          # a traves del plugin de Shelly
        else:
            if not self._warned_direct_meter:
                log.warning("Seguimiento de carga: el plugin de Shelly no tiene el contador %s dado de alta "
                            "-- se lee directamente", host)
                self._warned_direct_meter = True
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
        # Lo que hace de verdad cada equipo (su puerto de red por BLE) para
        # aprender su respuesta: entrega = grid_w > 0; carga por alterna =
        # lo que toma de red menos lo que sale por sus enchufes.
        for f in ctx.get("followers") or []:
            g = f.get("grid_w")
            if g is not None:
                self._learner.observe(f["id"], "base", now, max(0.0, g))
                self._learner.observe(f["id"], "charge", now, max(0.0, -g - (f.get("outlets_w") or 0.0)))
        # Ordenes que el equipo no confirmo (envio fallido o verificacion): se
        # olvidan para repetirlas en esta misma lectura.
        with self._lock:
            failed, self._failed = self._failed, set()
        for bid in failed:
            self._current.pop(bid, None)
        followers = ctx.get("followers") or []
        # Fuera del modo real nadie se queda con una carga base puesta. BUG REAL
        # (1-nov): al vencer la prueba el modo paso a sombra y la Ultra X se
        # quedo con 350 W de carga base -- la proxima descarga del plan los
        # habria entregado fijos, sin nadie siguiendo a la casa.
        self._release_missing({f["id"] for f in followers} if mode == "on" else set(), ctx)
        others = ctx.get("others") or []
        delivered = {k: self._estimate.get(k, v) for k, v in self._current.items()}
        charging = {k: self._charge_estimate.get(k, 0.0) for k in set(self._charge_current) | set(self._charge_estimate)}
        charge_targets = self._solar_charge_targets(meter, delivered, charging, followers, ctx)
        discharge_allowed = bool(ctx.get("discharge_allowed")) and not any(charge_targets.values())
        # Lo que cargan por alterna las que seguimos tambien lo ve el Shelly como
        # consumo: se descuenta igual que la carga de las demas.
        args = (meter, delivered, followers, discharge_allowed,
                float(ctx.get("contracted_w") or 0), _num_or(ctx.get("margin_w"), DEFAULT_MARGIN_W),
                float(ctx.get("other_charging_w") or 0) + sum(charging.values()), others,
                float(ctx.get("own_charging_w") or 0))
        wanted = wanted_total(*args)
        pool = followers + others
        self._active = choose_active(wanted, pool, self._active)
        if self._active and now - self._last_swap >= SWAP_MIN_SECONDS:
            swapped = maybe_swap(self._active, pool)
            if set(swapped) != set(self._active):
                self._last_swap = now
                self._active = swapped
        targets = compute_targets(*args, active=self._active)
        # Relevo de unidades: la que sale tarda en bajar (su retraso aprendido) y
        # la que entra sube enseguida -- entregaban las dos a la vez (3-nov 08:22:
        # -350 W de vertido). A la que entra solo se le da lo que falta
        # descontando lo que la saliente aun entrega, y sube a medida que la otra baja.
        lingering = sum(w for k, w in delivered.items() if k not in self._active and w > 0)
        if lingering > 0:
            total = sum(targets.get(k, 0.0) for k in self._active)
            if total > 0:
                factor = max(0.0, total - lingering) / total
                targets = {k: (v * factor if k in self._active else v) for k, v in targets.items()}
        # Subida limitada respecto a lo que ya entrega (bajadas inmediatas).
        targets = {k: min(v, delivered.get(k, 0.0) + self._step_up_w(k, "base")) if v > delivered.get(k, 0.0) else v
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
        # La que carga no descarga.
        for bid, w in charge_targets.items():
            if w > 0:
                targets[bid] = 0.0
        self._apply(targets, ctx, f"contador {meter:.0f} W")
        self._apply_charge(charge_targets, ctx, f"contador {meter:.0f} W")
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
                    b, check_base = by_id[bid], now - last_order >= VERIFY_AFTER_S

                    def verify_job(b=b, bid=bid, current=current, check_base=check_base):
                        if not self._verify(b, current, check_base):
                            with self._lock:
                                self._failed.add(bid)   # no aplicada: se vuelve a mandar
                    self._run_job(bid, verify_job)
        if mode == "on" and self._ensure is not None:
            by_id = {b["id"]: b for b in ctx.get("batteries_cfg") or []}
            for bid, w in self._current.items():
                if w > 0 and bid in by_id and now - self._last_ensure.get(bid, 0.0) >= ENSURE_TASKS_SECONDS:
                    self._last_ensure[bid] = now
                    self._run_job(bid, lambda b=by_id[bid]: self._ensure(b))

    def _step_up_w(self, bid: str, kind: str) -> float:
        """Cuanto puede subir esa bateria entre dos lecturas: su ritmo aprendido,
        con un STEP_UP_HEADROOM de margen para poder descubrir que es mas
        rapida de lo que se creia (si el tope fuera justo lo aprendido, nunca
        se le pediria mas y nunca se veria)."""
        return self._learner.get(bid, f"{kind}_up")["ramp"] * POLL_SECONDS * STEP_UP_HEADROOM

    def _solar_charge_targets(self, meter: float, delivered: dict[str, float], charging: dict[str, float],
                              followers: list[dict], ctx: dict) -> dict[str, float]:
        """Limite de carga de cada seguidor con el excedente de ahora (todas a 0
        si no toca). Solo en modo real, con la funcion activada y sin carga de
        red planificada esta hora (esa la fija el plan)."""
        on = (self._charge is not None and (ctx.get("mode") or "off") == "on"
              and ctx.get("solar_charge", True) and not ctx.get("grid_charge_now"))
        if not on:
            return {bid: 0.0 for bid, w in self._charge_current.items() if w > 0}
        net = house_net_w(meter, delivered, charging)
        charging_now = any(w > 0 for w in self._charge_current.values())
        if not charging_now:
            now = time.monotonic()
            # Un vertido mientras alguna descarga o acaba de descargar es exceso
            # de la DESCARGA (p.ej. un relevo de unidades, 3-nov 08:22: -350 W),
            # no sol: lo que toca es reducirla, no cargar.
            discharging = any(w > 0 for w in self._current.values()) or any(w > 1 for w in delivered.values())
            if discharging or -net < SOLAR_CHARGE_START_W:
                self._surplus_since = None
                return {}
            # Y tiene que mantenerse mas de lo que tardan las baterias en
            # responder (lo aprendido de cada una), no un pico de un segundo.
            if self._surplus_since is None:
                self._surplus_since = now
            hold = 2 * max([self._learner.get(f["id"], "charge_up")["delay"] for f in followers] or [CHARGE_RESPONSE_DELAY_S])
            if now - self._surplus_since < hold:
                return {}
        targets = solar_charge_targets(net, followers, charging_now)
        # Subida limitada (como la descarga): no perseguir un claro corto de sol.
        targets = {k: min(v, charging.get(k, 0.0) + self._step_up_w(k, "charge")) if v > charging.get(k, 0.0) else v
                   for k, v in targets.items()}
        for bid, w in self._charge_current.items():
            if w > 0 and bid not in targets:
                targets[bid] = 0.0
        return targets

    def _apply_charge(self, targets: dict[str, float], ctx: dict, why: str) -> None:
        if not targets or self._charge is None:
            return
        if (ctx.get("mode") or "off") != "on":
            targets = {k: 0.0 for k in targets}      # fuera del modo real solo se para
        by_id = {b["id"]: b for b in ctx.get("batteries_cfg") or []}
        changes = {bid: w for bid, w in targets.items()
                   if abs(w - self._charge_current.get(bid, 0.0)) >= 1 or (w == 0 and self._charge_current.get(bid, 0.0) > 0)}
        for bid, w in changes.items():
            b = by_id.get(bid)
            if b is None:
                continue

            def charge_job(b=b, w=w, bid=bid):
                if not self._charge(b, w):
                    log.warning("[SEGUIMIENTO] %s: no se pudo aplicar el limite de carga de %.0f W -- se reintenta",
                                b.get("name", bid), w)
                    with self._lock:
                        self._charge_current.pop(bid, None)
            if self.asynchronous:
                self._worker(bid).charge(charge_job)
            else:
                charge_job()
            self._learner.order(bid, "charge", time.monotonic(), self._charge_estimate.get(bid, 0.0), w)
            self._charge_orders.setdefault(bid, []).append((time.monotonic(), w))
            if w > 0:
                self._charge_current[bid] = w
            else:
                # La estimacion baja sola cuando el equipo aplique el 0 (~2.5 s).
                self._charge_current.pop(bid, None)
        now = time.monotonic()
        if changes and now - self._last_charge_log >= SHADOW_LOG_SECONDS:
            self._last_charge_log = now
            log.info("[SEGUIMIENTO CARGA SOLAR] %s -> %s", why, ", ".join(
                f"{(by_id.get(b) or {}).get('name', b)} {w:.0f} W" for b, w in targets.items()))

    def solar_charging_ids(self) -> set[str]:
        """Seguidores que estan cargando ahora con excedente: el plan no les
        manda ordenes de carga/descarga mientras tanto."""
        return {bid for bid, w in self._charge_current.items() if w > 0}

    def _worker(self, bid: str) -> _Worker:
        if bid not in self._workers:
            self._workers[bid] = _Worker(bid)
        return self._workers[bid]

    def _run_job(self, bid: str, fn) -> None:
        if self.asynchronous:
            self._worker(bid).job(fn)
        else:
            fn()

    def _run_base(self, bid: str, fn) -> None:
        if self.asynchronous:
            self._worker(bid).base(fn)
        else:
            fn()

    def _apply(self, targets: dict[str, float], ctx: dict, why: str) -> None:
        mode = ctx.get("mode") or "off"
        # 0 = seguir cada lectura (decision del usuario, 2-nov: hay un adaptador
        # BLE por cada dos baterias y el hilo de cada equipo agrupa las ordenes
        # que lleguen antes de poder mandarlas). Por debajo de 1 W no se reenvia
        # la misma orden.
        deadband = max(1.0, _num_or(ctx.get("deadband_w"), DEFAULT_DEADBAND_W))
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

            def send_job(b=b, bid=bid, w=w):
                if not self._send(b, w):
                    log.warning("[SEGUIMIENTO] %s: no se pudo aplicar la carga base de %.0f W -- se reintenta",
                                b.get("name", bid), w)
                    with self._lock:
                        self._failed.add(bid)
            # Se da por enviada al ponerla en cola; si el equipo no la confirma,
            # el hilo la marca como fallida y la verificacion la detecta.
            self._run_base(bid, send_job)
            self._learner.order(bid, "base", time.monotonic(), self._estimate.get(bid, self._current.get(bid, 0.0)), w)
            self._orders.setdefault(bid, []).append((time.monotonic(), w))
            # No se reprograma una verificacion pendiente: mandando cada
            # segundo no llegaria nunca.
            self._verify_due.setdefault(bid, (time.monotonic() + VERIFY_AFTER_S, w))
            self._current[bid] = w
            self._followed.add(bid)
        now = time.monotonic()
        if changes and now - self._last_log >= SHADOW_LOG_SECONDS:
            self._last_log = now
            log.info("[SEGUIMIENTO] %s -> %s", why, ", ".join(
                f"{(by_id.get(b) or {}).get('name', b)} {w:.0f} W" for b, w in targets.items()))

    def _advance_estimates(self, now: float) -> None:
        """Lo entregado de verdad va hacia la orden que el equipo ya esta
        aplicando, con el retraso y el ritmo APRENDIDOS de esa bateria (ver
        ResponseLearner), por separado al subir y al bajar. BUG REAL de la primera version: cada
        orden nueva reiniciaba el retraso, y mandando cada segundo la estimacion
        se quedaba congelada -- el seguimiento no veia el efecto de lo que
        mandaba y oscilaba."""
        dt = 0.0 if self._last_advance is None else max(0.0, now - self._last_advance)
        self._last_advance = now
        for kind, orders, estimate in (("base", self._orders, self._estimate),
                                       ("charge", self._charge_orders, self._charge_estimate)):
            for bid, history in orders.items():
                up, down = self._learner.get(bid, f"{kind}_up"), self._learner.get(bid, f"{kind}_down")
                applied_up = applied_down = None
                for ts, w in history:
                    if ts <= now - up["delay"]:
                        applied_up = w
                    if ts <= now - down["delay"]:
                        applied_down = w
                # Solo hace falta la ultima orden ya aplicada y las posteriores.
                keep_after = now - max(up["delay"], down["delay"])
                while len(history) > 1 and history[1][0] <= keep_after:
                    history.pop(0)
                est = estimate.get(bid, 0.0)
                if applied_up is not None and applied_up > est:
                    estimate[bid] = min(applied_up, est + up["ramp"] * dt)
                elif applied_down is not None and applied_down < est:
                    estimate[bid] = max(applied_down, est - down["ramp"] * dt)

    def _release_missing(self, still: set[str], ctx: dict) -> None:
        """Las baterias que seguian y ya no: a 0 y devueltas al planificador."""
        by_id = {b["id"]: b for b in ctx.get("batteries_cfg") or []}
        for bid in [x for x in self._followed if x not in still]:
            b = by_id.get(bid)
            if b is not None and self._release is not None:
                # Ninguna carga base pendiente puede mandarse ya despues de liberar.
                if bid in self._workers:
                    self._workers[bid].cancel_base()

                def release_job(b=b):
                    for attempt in range(RELEASE_ATTEMPTS):
                        if self._release(b):
                            return
                        if attempt + 1 < RELEASE_ATTEMPTS:
                            time.sleep(RELEASE_RETRY_S)
                    log.warning("[SEGUIMIENTO] %s: no se pudo liberar tras %d intentos (carga base sin confirmar a 0)",
                                b.get("name", b.get("id")), RELEASE_ATTEMPTS)
                self._run_job(bid, release_job)
            if True:
                self._followed.discard(bid)
                self._current.pop(bid, None)
                self._estimate.pop(bid, None)
                self._orders.pop(bid, None)
                self._active = [a for a in self._active if a != bid]
                log.info("[SEGUIMIENTO] %s deja de seguir: carga base a 0 y vuelve al planificador",
                         (b or {}).get("name", bid))
