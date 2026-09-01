"""
Motor de planificacion de carga/descarga de baterias.

Nada de programacion lineal ni parametros ocultos: dos pasadas simples,
explicables y deterministas.

  PASADA A (hacia atras): cuanta energia hace falta reservar para cubrir
  las horas caras (punta) que quedan por delante, dado el consumo previsto
  y la produccion solar prevista.

  PASADA B (hacia adelante): simula hora a hora. Carga siempre gratis con
  excedente solar. Carga desde red SOLO en horas valle y SOLO lo que falte
  para llegar a la reserva calculada en la pasada A (nunca de mas). Descarga
  en horas punta (y llano si sobra) para cubrir el deficit previsto — y
  tambien en valle, pero solo con lo que sobre por encima de esa reserva
  (p.ej. tras un dia de mucho sol con buena previsión para el siguiente).

El resultado es un plan hora a hora, mas la accion concreta a ejecutar YA
en la hora actual.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# Margen sobre la potencia sostenida calculada (carga_pausada): la prevision
# de consumo/solar puede fallar un poco, asi que apuntamos a llegar con un
# 20% de mas potencia de la estrictamente necesaria, no al filo.
PACED_CHARGE_SAFETY_MARGIN = 1.2


@dataclass
class HourPlan:
    dt: datetime
    price: float
    tier: str
    pv_w: float
    load_w: float
    charge_w: float = 0.0
    discharge_w: float = 0.0
    soc_wh: float = 0.0
    reason: str = ""
    charge_source: str | None = None  # "solar" | "grid" | None — de donde sale la carga de esta hora


def build_plan(
    now: datetime,
    pv_forecast_w: list[float],
    load_forecast_w: list[float],
    current_soc_wh: float,
    total_capacity_wh: float,
    max_charge_w: float,
    max_discharge_w: float,
    min_soc_wh: float,
    prices_tiers: list[tuple[float, str]],
    contracted_power_w: float = 0,
    max_usable_wh: float | None = None,
    allow_grid_charging: bool = True,
    paced_charging: bool = False,
    reserve_safety_margin_wh: float = 0.0,
) -> tuple[list[HourPlan], float]:
    """
    pv_forecast_w / load_forecast_w: listas de potencia media (W) para cada
    una de las proximas horas, empezando por la hora actual (indice 0).
    Ambas listas deben tener la misma longitud (el horizonte, tipicamente 24-36h).

    prices_tiers: lista de (precio EUR/kWh, tramo) para cada hora del mismo
    horizonte, ya calculada por el modulo `tariff_source` (tarifa fija o
    PVPC dinamica) — a este motor le da igual de donde vengan los precios.

    contracted_power_w: potencia contratada de la vivienda (0 = sin limite).
    Solo se aplica a la carga desde RED (en valle) — la carga con excedente
    solar no consume potencia contratada porque no tira de red.

    max_usable_wh: techo real de carga (p.ej. si tus baterias tienen un
    SOC maximo declarado por debajo del 100% nominal, como 97%, para
    alargar su vida util). Si no se indica, se usa total_capacity_wh
    (100% nominal). total_capacity_wh se sigue usando tal cual para
    calcular el % de SOC en el plan.

    allow_grid_charging: si es False, la bateria SOLO carga con excedente
    solar — nunca desde red, ni en valle ni en emergencia de llano (modo
    "autoconsumo"). Si es True (por defecto), tambien carga desde red en
    valle (y en llano de emergencia) lo justo para cubrir la punta que
    quede por delante (modo "ahorro").

    paced_charging: si es True, la carga deliberada desde red (valle y
    emergencia en llano) NO va siempre a maxima potencia: se reparte a lo
    largo de las horas que quedan hasta que la bateria vaya a hacer falta
    de verdad (la proxima hora, sea llano o punta, en la que se prevea
    consumo por encima de la produccion solar), con un margen de seguridad.
    Menos calor/estres en la bateria a cambio de, a veces, no acabar de
    cargar tan rapido. Si el tiempo se echa encima, la potencia calculada
    sube sola (mismo calculo, menos horas) hasta el maximo si hace falta —
    no es una rama de emergencia aparte, es el mismo numero.

    reserve_safety_margin_wh: colchon extra (Wh) que se suma a CUALQUIER
    objetivo de reserva calculado (punta/llano futuros) antes de decidir si
    cargar en valle o si descargar el sobrante en valle/llano — nunca se
    toca en la descarga de EMERGENCIA de punta (rama 3), que siempre puede
    llegar hasta min_soc_wh de verdad si hace falta. Sin margen, la reserva
    apunta exactamente a lo que la previsión dice que hara falta; si la
    previsión falla un poco (sol real por debajo de lo previsto, consumo
    real por encima), la bateria puede acabar tocando el suelo de verdad
    varias horas seguidas sin colchon ninguno — sobre todo en bloques de
    valle largos (p.ej. fin de semana entero) donde no se ve ningun tramo
    caro dentro del horizonte y la reserva calculada es minima o nula. 0.0
    (por defecto) reproduce el comportamiento de siempre, al filo.

    Devuelve (plan, reserve_wh): el plan hora a hora, y el nivel de SOC
    absoluto (Wh) que el motor esta intentando alcanzar AHORA MISMO para
    cubrir punta y llano futuros — util para mostrar "cuanto falta para
    la reserva" en la interfaz sin duplicar esta cuenta en otro sitio.
    """
    # BUG REAL, confirmado por fuzzing adversarial: sin esta guarda, un
    # horizonte vacio (`pv_forecast_w=[]`) o unas listas descuadradas entre
    # si (p.ej. `load_forecast_w`/`prices_tiers` mas cortas que
    # `pv_forecast_w`, que es quien define `horizon`) tiran `IndexError`
    # sin capturar en tres puntos distintos mas abajo (linea 183 con
    # horizonte 0, y dentro de las comprensiones/bucles de la Pasada A con
    # listas descuadradas) -- el ciclo de planificacion entero se caia en
    # vez de fallar con un mensaje claro o degradar con gracia. Horizonte 0
    # es un caso legitimo (p.ej. una fuente de datos momentaneamente sin
    # nada que ofrecer) y se resuelve solo, sin plan que hacer; longitudes
    # descuadradas SON un bug de quien llama y merecen un error explicito,
    # no un IndexError críptico. `prices_tiers` mas LARGO que el horizonte
    # no es un problema (se ignoran los sobrantes, como ya pasaba antes).
    horizon = len(pv_forecast_w)
    if horizon == 0:
        return [], min_soc_wh
    if len(load_forecast_w) < horizon or len(prices_tiers) < horizon:
        raise ValueError(
            "build_plan: pv_forecast_w/load_forecast_w/prices_tiers deben cubrir "
            f"al menos el mismo horizonte ({horizon}h) -- longitudes recibidas: "
            f"pv={horizon}, load={len(load_forecast_w)}, prices={len(prices_tiers)}."
        )
    hours = [now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=i) for i in range(horizon)]
    ceiling_wh = max_usable_wh if max_usable_wh is not None else total_capacity_wh
    usable_capacity_wh = ceiling_wh - min_soc_wh

    deficit_w = [max(0.0, load_forecast_w[i] - pv_forecast_w[i]) for i in range(horizon)]
    surplus_w = [max(0.0, pv_forecast_w[i] - load_forecast_w[i]) for i in range(horizon)]

    # --- PASADA A: cuanto deficit de punta y de llano quedan por delante
    # desde cada hora i, SOLO hasta la proxima hora valle (sin incluirla)
    # — un valle es una nueva oportunidad de recarga barata, asi que lo
    # que venga DESPUES de ese valle ya se cubrira entonces, no hace falta
    # reservarlo ahora mismo. Sin este corte (el bug que arreglaba la
    # version anterior de esto), una punta o llano de mañana se sumaba a
    # la de hoy y el motor cargaba de mas en valle esta noche, dejando
    # menos hueco para el sol de mañana — o forzaba cargas de emergencia
    # en llano, o se negaba a descargar en llano/valle, aunque un valle
    # previo ya fuera a recargar de sobra.
    future_punta_after = [0.0] * (horizon + 1)
    future_llano_after = [0.0] * (horizon + 1)
    for i in range(horizon - 1, -1, -1):
        if prices_tiers[i][1] == "valle":
            future_punta_after[i] = 0.0
            future_llano_after[i] = 0.0
        else:
            future_punta_after[i] = future_punta_after[i + 1] + (deficit_w[i] if prices_tiers[i][1] == "punta" else 0.0)
            future_llano_after[i] = future_llano_after[i + 1] + (deficit_w[i] if prices_tiers[i][1] == "llano" else 0.0)
    # future_punta_after[i] / future_llano_after[i] = deficit en ese tramo
    # desde la hora i (inclusive) hasta la proxima hora valle (sin pasar de ahi)

    # Las dos lineas de arriba resetean a 0 EN CADA hora de valle (por
    # diseño: sirven para llano/punta, que preguntan "cuanto queda hasta
    # el proximo valle" y ahi mismo, en un valle, la respuesta es "nada,
    # ya estoy en uno"). Pero para decidir CUANTO CARGAR mientras se esta
    # EN valle (ramas 2 y 5, mas abajo) hace falta lo contrario: todas las
    # horas de un mismo bloque de valle seguido deben apuntar al MISMO
    # objetivo — lo que haga falta para el tramo no-valle que viene justo
    # despues — para poder repartir la carga a lo largo de todo el valle,
    # no solo en su ultima hora (que es donde future_*_after[i] deja de
    # estar a 0). Se propaga hacia atras el valor de la primera hora
    # no-valle que se encuentre.
    valle_target_punta = [0.0] * (horizon + 1)
    valle_target_llano = [0.0] * (horizon + 1)
    for i in range(horizon - 1, -1, -1):
        if prices_tiers[i][1] == "valle":
            valle_target_punta[i] = valle_target_punta[i + 1] if i + 1 < horizon else 0.0
            valle_target_llano[i] = valle_target_llano[i + 1] if i + 1 < horizon else 0.0
        else:
            valle_target_punta[i] = future_punta_after[i]
            valle_target_llano[i] = future_llano_after[i]

    def _reserve_target(i: int) -> float:
        """
        Nivel ABSOLUTO de SOC (Wh) que hace falta alcanzar, visto desde la
        hora valle i, para cubrir el punta y llano del tramo no-valle que
        viene justo despues de este bloque de valle — punta primero, y
        con la capacidad que quede, llano. Es el objetivo que usa tanto la
        carga en valle (rama 2) como la descarga de excedente en valle
        (rama 5); solo tiene sentido llamarla en horas de valle.
        """
        energy_needed = min(valle_target_punta[i], usable_capacity_wh)
        energy_needed += min(valle_target_llano[i], max(0.0, usable_capacity_wh - energy_needed))
        # BUG REAL DE SEGURIDAD, confirmado por fuzzing adversarial: si
        # `max_usable_wh` (via `ceiling_wh`) queda por DEBAJO de
        # `min_soc_wh` -- config contradictoria pero perfectamente posible
        # por error de usuario (p.ej. bajar el techo de SOC desde la
        # interfaz sin subir antes el suelo) -- `usable_capacity_wh` sale
        # NEGATIVO y el `min(ceiling_wh, ...)` de abajo podia devolver un
        # objetivo de reserva por DEBAJO del propio suelo declarado. La
        # rama 5 (descarga en valle) usa este valor tal cual como limite
        # de cuanto puede vaciar la bateria, asi que un objetivo mal
        # saturado la mandaba a descargar por debajo de `min_soc_wh` de
        # verdad -- confirmado en pruebas: 200 Wh por debajo del suelo. El
        # `max(min_soc_wh, ...)` de fuera garantiza que el objetivo NUNCA
        # cae por debajo del suelo fisico, pase lo que pase con la config.
        return max(min_soc_wh, min(ceiling_wh, min_soc_wh + energy_needed + reserve_safety_margin_wh))

    # reserve_wh (para mostrar "cuanto hace falta ahora mismo"): si la
    # hora actual es valle, usa el objetivo propagado del bloque de valle
    # (lo de arriba); si no, el corte normal hasta el proximo valle.
    if prices_tiers[0][1] == "valle":
        reserve_wh = _reserve_target(0)
    else:
        energy_needed_now = min(future_punta_after[0], usable_capacity_wh)
        energy_needed_now += min(future_llano_after[0], max(0.0, usable_capacity_wh - energy_needed_now))
        reserve_wh = min(ceiling_wh, min_soc_wh + energy_needed_now + reserve_safety_margin_wh)

    # Para la carga sostenida (paced_charging): en que hora, de aqui en
    # adelante, va a hacer falta de verdad la bateria por primera vez — sea
    # llano o punta, nos da igual el tramo, solo que haya consumo por
    # encima del solar previsto (en valle nunca se descarga, se paga barato
    # directo de red, asi que no cuenta como "necesidad").
    needs_battery = [prices_tiers[i][1] != "valle" and deficit_w[i] > 0 for i in range(horizon)]
    next_need_idx: list[int | None] = [None] * (horizon + 1)
    for i in range(horizon - 1, -1, -1):
        next_need_idx[i] = i if needs_battery[i] else next_need_idx[i + 1]

    def _paced_charge_limit(i: int, soc_now: float, target_wh: float) -> float:
        """Potencia sostenida que reparte lo que falta hasta que la bateria
        haga falta por primera vez. Si esa necesidad es AHORA MISMO (o no
        se ve ninguna en el horizonte), no hay margen que repartir: maxima
        potencia disponible, sin rodeos."""
        # BUG REAL, confirmado por fuzzing adversarial: mirar `next_need_idx[i]`
        # (en vez de `[i + 1]`) hacia la propia hora `i` que se esta
        # decidiendo AHORA MISMO -- si `i` es una hora de llano con deficit
        # (justo el caso de la rama 2b, carga de emergencia), `needs_battery[i]`
        # sale True para esa MISMA hora en la que se esta cargando, asi que
        # `deadline == i`, `hours_remaining == 0` y la funcion devuelve
        # `max_charge_w` sin repartir nada -- justo el escenario limite que
        # `paced_charging` deberia cubrir mejor (0h de valle antes de la
        # punta, toda la carga cae en la rama de emergencia). Mirar desde
        # `i + 1` pregunta "cuando hace falta la bateria DESPUES de esta
        # hora", que es la pregunta correcta cuando la hora actual es
        # precisamente la que esta cargando. Para la rama 2 (carga en
        # valle) esto no cambia nada: `needs_battery[i]` ya es False en
        # cualquier hora de valle por construccion (linea de `needs_battery`
        # de arriba exige tramo != "valle"), asi que `next_need_idx[i]` y
        # `next_need_idx[i+1]` ya coincidian siempre ahi.
        deadline = next_need_idx[i + 1]
        if deadline is None:
            return max_charge_w
        hours_remaining = deadline - i
        if hours_remaining <= 0:
            return max_charge_w
        energy_needed_wh = max(0.0, target_wh - soc_now)
        return min(max_charge_w, (energy_needed_wh / hours_remaining) * PACED_CHARGE_SAFETY_MARGIN)

    # --- PASADA B: simulacion hacia adelante ---
    plan: list[HourPlan] = []
    soc = current_soc_wh

    for i in range(horizon):
        price, tier = prices_tiers[i]
        hp = HourPlan(dt=hours[i], price=price, tier=tier, pv_w=pv_forecast_w[i], load_w=load_forecast_w[i])

        # 1) Carga gratis con excedente solar, siempre.
        if surplus_w[i] > 0:
            headroom = ceiling_wh - soc
            charge = min(surplus_w[i], max_charge_w, headroom)
            if charge > 0:
                soc += charge
                hp.charge_w = charge
                hp.charge_source = "solar"
                hp.reason = "carga con excedente solar"

        # 2) Carga desde red en VALLE, oportunista, hasta la reserva completa
        #    (punta + llano que quepa). Respetando la potencia contratada.
        #    Se salta por completo en modo "autoconsumo" (allow_grid_charging=False).
        elif allow_grid_charging and tier == "valle" and soc < _reserve_target(i):
            target = _reserve_target(i)
            headroom = min(ceiling_wh - soc, target - soc)
            charge_limit = _paced_charge_limit(i, soc, target) if paced_charging else max_charge_w
            if contracted_power_w > 0:
                grid_headroom = max(0.0, contracted_power_w - load_forecast_w[i])
                charge_limit = min(charge_limit, grid_headroom)
            charge = min(charge_limit, headroom)
            if charge > 0:
                soc += charge
                hp.charge_w = charge
                hp.charge_source = "grid"
                if paced_charging and charge_limit < max_charge_w:
                    hp.reason = f"carga sostenida en valle (objetivo reserva {target/1000:.2f} kWh)"
                else:
                    hp.reason = f"carga en valle (objetivo reserva {target/1000:.2f} kWh)"
            elif charge_limit <= 0:
                hp.reason = "sin carga: al limite de potencia contratada"

        # 2b) Carga de EMERGENCIA en LLANO: si con lo que hay no va a llegar
        #     a cubrir toda la punta que queda por delante, compensa cargar
        #     en llano aunque sea mas caro que valle — sigue siendo mas
        #     barato que dejar esa punta sin cubrir (llano < punta siempre).
        #     Solo carga lo justo para tapar ese hueco, no la reserva completa.
        #     Tambien se salta en modo "autoconsumo".
        # BUG REAL, confirmado por fuzzing adversarial: la condicion
        # comparaba `soc` contra `min_soc_wh + future_punta_after[i]` SIN
        # capar al techo real de la bateria (`ceiling_wh`) -- `target`,
        # dos lineas mas abajo, SI lo capa. Con un deficit de punta futuro
        # que excede la capacidad util (facil: un pico de consumo grande
        # en un dia sin apenas sol), la condicion salia True aunque la
        # bateria ya estuviera al 100% y no hubiera ningun hueco real que
        # cargar (`charge` acababa siendo 0) -- y al entrar en esta rama
        # `elif` sin poder cargar nada, la rama 4 (descarga en llano) NUNCA
        # se evaluaba para esa hora, dejando un deficit real de llano sin
        # cubrir con bateria disponible, comprado a red sin necesidad.
        elif allow_grid_charging and tier == "llano" and soc < min(ceiling_wh, min_soc_wh + future_punta_after[i]):
            target = min(ceiling_wh, min_soc_wh + future_punta_after[i])
            headroom = min(ceiling_wh - soc, target - soc)
            charge_limit = _paced_charge_limit(i, soc, target) if paced_charging else max_charge_w
            if contracted_power_w > 0:
                grid_headroom = max(0.0, contracted_power_w - load_forecast_w[i])
                charge_limit = min(charge_limit, grid_headroom)
            charge = min(charge_limit, headroom)
            if charge > 0:
                soc += charge
                hp.charge_w = charge
                hp.charge_source = "grid"
                if paced_charging and charge_limit < max_charge_w:
                    hp.reason = "carga sostenida en llano (no llegaba a cubrir la punta que queda)"
                else:
                    hp.reason = "carga en llano (no llegaba a cubrir la punta que queda)"
            elif charge_limit <= 0:
                hp.reason = "sin carga: al limite de potencia contratada"

        # 3) Descarga en PUNTA: siempre prioritaria, cubre el deficit previsto.
        elif deficit_w[i] > 0 and tier == "punta":
            available = max(0.0, soc - min_soc_wh)
            discharge = min(deficit_w[i], max_discharge_w, available)
            if discharge > 0:
                soc -= discharge
                hp.discharge_w = discharge
                hp.reason = "descarga para cubrir consumo en punta"

        # 4) Descarga en LLANO: solo con lo que sobre por encima de lo que
        #    haga falta reservar para TODA la punta que quede por delante.
        elif deficit_w[i] > 0 and tier == "llano":
            reserved_for_future_punta = future_punta_after[i + 1]
            available = max(0.0, soc - min_soc_wh - reserved_for_future_punta - reserve_safety_margin_wh)
            discharge = min(deficit_w[i], max_discharge_w, available)
            if discharge > 0:
                soc -= discharge
                hp.discharge_w = discharge
                hp.reason = "descarga para cubrir consumo en llano"
            else:
                hp.reason = "sin descargar en llano: reservado para punta posterior"

        # 5) Descarga en VALLE con lo que sobre por encima de la reserva:
        #    si ya hay mas SOC del que hace falta para cubrir toda la punta
        #    y llano futuros (p.ej. tras un dia de mucho sol, con previsión
        #    de que el dia siguiente tambien se cubra bien solo), no tiene
        #    sentido comprar a red ese consumo — aunque valle ya sea barato,
        #    es dinero de mas gastado en energia que ya tienes almacenada.
        #    Ademas libera hueco para no desperdiciar el excedente solar de
        #    mañana. Nunca toca la reserva: solo gasta lo que sobra de ella.
        elif deficit_w[i] > 0 and tier == "valle":
            available = max(0.0, soc - _reserve_target(i))
            discharge = min(deficit_w[i], max_discharge_w, available)
            if discharge > 0:
                soc -= discharge
                hp.discharge_w = discharge
                hp.reason = "descarga en valle: reserva de punta/llano ya cubierta, evita comprar de mas"

        if not hp.reason:
            hp.reason = "sin accion (no compensa)"

        hp.soc_wh = soc
        plan.append(hp)

    return plan, reserve_wh


if __name__ == "__main__":
    # Prueba rapida con datos simulados: un domingo por la tarde, poca
    # bateria, punta manana laborable.
    from tariff_source import FixedTariffConfig, fixed_tariff_prices

    now = datetime(2026, 8, 2, 18, 0)  # domingo 18:00, como en la conversacion real
    horizon = 24

    # Consumo tipico: base ~250W, pico tarde 15-17h laborable no incluido aqui.
    load = [340, 782, 489, 552, 372, 283, 259, 246, 225, 267, 226, 212, 192, 195,
            359, 553, 790, 933, 689, 554, 438, 1373, 2465, 1012][:horizon]
    pv = [298, 76, 26, 4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
          9, 45, 171, 303, 397, 421, 390, 404, 409, 292][:horizon]

    cfg = FixedTariffConfig()
    prices_tiers = fixed_tariff_prices(now, horizon, cfg)
    plan, reserve_wh = build_plan(
        now=now,
        pv_forecast_w=pv,
        load_forecast_w=load,
        current_soc_wh=0.50 * 9600,
        total_capacity_wh=9600,
        max_charge_w=1200,
        max_discharge_w=1200,
        min_soc_wh=0.03 * 9600,
        prices_tiers=prices_tiers,
    )

    print(f"{'Hora':>16} {'Tramo':>6} {'Precio':>7} {'PV':>6} {'Carga':>7} {'Carga_W':>8} {'Desc_W':>7} {'SOC%':>6}  Motivo")
    for hp in plan:
        print(f"{hp.dt.strftime('%a %d %H:%M'):>16} {hp.tier:>6} {hp.price:>7.3f} {hp.pv_w:>6.0f} "
              f"{hp.load_w:>7.0f} {hp.charge_w:>8.0f} {hp.discharge_w:>7.0f} {100*hp.soc_wh/9600:>5.1f}%  {hp.reason}")
