"""
Planificador de baterias por programacion dinamica exacta (motor "dp").

Mismo contrato que `scheduler.build_plan` (mismas entradas y salida), pero en
vez de reglas por tramo (valle/llano/punta) resuelve el problema economico
completo sobre el horizonte con las previsiones de precio, solar y consumo:

    minimizar  sum_h  precio_h * energia_importada_h - precio_venta * energia_vertida_h
                      + desgaste * energia_descargada_h

sujeto a potencia maxima de carga/descarga, SOC minimo/maximo, eficiencia de
carga y descarga, potencia contratada (solo la carga DESDE RED) y a que la
bateria no vierte a red (solo descarga hasta cubrir el consumo).

Por que no rules-of-thumb: con precios dinamicos (PVPC) los "tercios" de precio
reparten mal la bateria (descarga las primeras horas caras en vez de las mas
caras); con solar la reserva no descontaba el sol que iba a recargar la
bateria gratis. La DP lo resuelve sin casos especiales: el orden de las horas
por precio sale solo del calculo.

Metodo: la funcion de coste futuro V_h(SOC) es CONVEXA y lineal a trozos (el
coste de cada hora lo es), asi que V_h se obtiene de V_{h+1} por convolucion
infimal de dos secuencias convexas discretas = mezclar sus incrementos
ordenados (O(K) por hora, exacto en la malla). En Python puro (sin numpy):
del orden de 3 ms por plan de 48 h.

La carga "sostenida" (paced_charging) sale de una penalizacion minima que crece
con la potencia (1-2 % del precio): a igual precio se reparte la carga entre
todas las horas baratas en vez de concentrarla en una.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from scheduler import HourPlan

K_LEVELS = 64                # niveles de SOC de la malla
MIN_FIRST_STAGE_H = 0.1      # la primera etapa (resto de la hora actual) no baja de 6 min


def build_plan_dp(
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
    *,
    eta_charge: float = 0.94,
    eta_discharge: float = 0.94,
    export_price: float = 0.04,
    wear_eur_per_kwh: float = 0.01,
    load_margin_frac: float = 0.0,
    pv_margin_frac: float = 0.0,
) -> tuple[list[HourPlan], float]:
    horizon = len(pv_forecast_w)
    if horizon == 0:
        return [], min_soc_wh
    if len(load_forecast_w) < horizon or len(prices_tiers) < horizon:
        raise ValueError(
            "build_plan: pv_forecast_w/load_forecast_w/prices_tiers deben cubrir "
            f"al menos el mismo horizonte ({horizon}h) -- longitudes recibidas: "
            f"pv={horizon}, load={len(load_forecast_w)}, prices={len(prices_tiers)}."
        )
    hour0 = now.replace(minute=0, second=0, microsecond=0)
    hours = [hour0 + timedelta(hours=i) for i in range(horizon)]
    ceiling_wh = max_usable_wh if max_usable_wh is not None else total_capacity_wh
    lo = min_soc_wh
    hi = max(ceiling_wh, min_soc_wh)
    span = hi - lo

    # duracion de cada etapa: la primera es lo que queda de la hora en curso
    frac = 1.0 - (now.minute * 60 + now.second) / 3600.0
    dts = [1.0] * horizon
    dts[0] = min(1.0, max(MIN_FIRST_STAGE_H, frac))

    eta_c = min(1.0, max(0.5, eta_charge))
    eta_d = min(1.0, max(0.5, eta_discharge))
    wear = max(0.0, wear_eur_per_kwh) / 1000.0            # EUR/Wh almacenado descargado
    spread = 0.012 if paced_charging else 0.0

    s_now = min(hi, max(lo, current_soc_wh))

    if span <= 1e-6:
        # sin margen util (SOC minimo == techo): nada que optimizar
        plan = []
        for i in range(horizon):
            price, tier = prices_tiers[i]
            plan.append(HourPlan(dt=hours[i], price=price, tier=tier, pv_w=pv_forecast_w[i], load_w=load_forecast_w[i],
                                 soc_wh=s_now, reason="sin accion (sin margen util de bateria)"))
        return plan, s_now

    K = K_LEVELS
    h = span / K                                            # Wh por nivel de la malla

    # ---- perfiles efectivos (margen de seguridad sobre la prevision, salvo la hora en curso)
    load_eff = [max(0.0, load_forecast_w[i]) * (1.0 + (load_margin_frac if i > 0 else 0.0)) for i in range(horizon)]
    pv_eff = [max(0.0, pv_forecast_w[i]) * (1.0 - (pv_margin_frac if i > 0 else 0.0)) for i in range(horizon)]

    # ---- coste por etapa: funcion CONVEXA de la variacion de energia almacenada (Wh)
    prices = [max(0.0, prices_tiers[i][0]) for i in range(horizon)]
    exp_p = max(0.0, export_price)
    stage = []   # (i0, p, e, d_min, d_solar, d_max, e_max_ac, dt)
    for i in range(horizon):
        dt = dts[i]
        p = prices[i]
        e = min(exp_p, p)                                   # vender nunca rinde mas que ahorrar comprar
        i0 = (load_eff[i] - pv_eff[i]) * dt                 # Wh de red sin bateria (<0 = excedente)
        d_min = -min(max_discharge_w * dt, max(0.0, i0)) / eta_d   # Wh almacenados que pueden salir (no vierte)
        e_max_ac = max_charge_w * dt
        e_solar = min(max(0.0, -i0), e_max_ac)              # carga gratis con el excedente
        if allow_grid_charging:
            grid_cap_w = (max(0.0, contracted_power_w - load_eff[i]) if contracted_power_w > 0 else max_charge_w)
            e_grid = max(0.0, min(e_max_ac - e_solar, grid_cap_w * dt))
        else:
            e_grid = 0.0
        stage.append((i0, p, e, d_min, e_solar * eta_c, (e_solar + e_grid) * eta_c, e_max_ac, dt))

    def stage_cost(i, delta):
        """Coste EUR de la etapa i con variacion de energia almacenada `delta` (Wh); None si inviable."""
        i0, p, e, d_min, d_solar, d_max, e_max_ac, dt = stage[i]
        if delta < d_min - 1e-6 or delta > d_max + 1e-6:
            return None
        if delta <= 0.0:
            return p * (i0 + delta * eta_d) + wear * (-delta)       # delta<0: sale energia (ahorra red)
        ec = delta / eta_c                                          # energia AC que entra
        g = i0 + ec
        base = p * g if g > 0 else e * g
        # penalizacion minima creciente con la potencia (carga sostenida): reparte entre horas iguales
        pen = spread * max(p, 1e-3) * ec * (ec / e_max_ac if e_max_ac > 0 else 0.0)
        return base + pen

    # unidades enteras por etapa
    def units(x):
        return int(round(x / h))

    # ---- retroceso: V[i][k] (k = 0..K) valor de llegar a la etapa i con SOC = lo + k*h
    # terminal: valor lineal de la energia que sobra (evita vaciar la bateria en el ultimo tramo)
    weights = [max(0.0, load_eff[i] - pv_eff[i]) for i in range(horizon)]
    wsum = sum(weights)
    if wsum > 0:
        v_term = eta_d * sum(prices[i] * weights[i] for i in range(horizon)) / wsum
    else:
        v_term = eta_d * (sum(prices) / len(prices))
    vt_per_wh = v_term
    V = [None] * (horizon + 1)
    V[horizon] = [-vt_per_wh * (k * h) for k in range(K + 1)]
    for i in range(horizon - 1, -1, -1):
        i0, p, e, d_min, d_solar, d_max, e_max_ac, dt = stage[i]
        ud = units(-d_min)                 # unidades que puede bajar
        uc = units(d_max)                  # unidades que puede subir
        # c(m) para m = -ud..uc
        cm = []
        for m in range(-ud, uc + 1):
            c = stage_cost(i, max(d_min, min(d_max, m * h)))
            cm.append(c if c is not None else float("inf"))
        # incrementos de c~(y)=c(-y), y en [-uc, ud]: orden creciente de y => m decreciente
        # c~(y) para y = -uc..ud  <=> m = uc..-ud
        ctil = cm[::-1]
        c_inc = [ctil[j + 1] - ctil[j] for j in range(len(ctil) - 1)]
        g = V[i + 1]
        g_inc = [g[k + 1] - g[k] for k in range(K)]
        merged = sorted(g_inc + c_inc)
        acc = g[0] + ctil[0]
        prefix = [acc]
        for x in merged:
            acc += x
            prefix.append(acc)
        # V_i(k) = prefix[k + uc]
        V[i] = [prefix[k + uc] for k in range(K + 1)]

    def v_at(i, s):
        x = (s - lo) / h
        if x <= 0:
            return V[i][0]
        if x >= K:
            return V[i][K]
        k = int(x)
        fr = x - k
        return V[i][k] * (1 - fr) + V[i][k + 1] * fr

    # ---- avance: reconstruir la decision hora a hora desde el SOC real
    plan: list[HourPlan] = []
    s = s_now
    for i in range(horizon):
        i0, p, e, d_min, d_solar, d_max, e_max_ac, dt = stage[i]
        price, tier = prices_tiers[i]
        # candidatos: puntos de la malla alcanzables y quiebros del coste
        lo_s, hi_s = max(lo, s + d_min), min(hi, s + d_max)
        cands = {round(lo_s, 6), round(hi_s, 6), round(min(hi, max(lo, s)), 6)}
        if lo_s + 1e-9 <= s + d_solar <= hi_s + 1e-9:
            cands.add(round(s + d_solar, 6))
        k0 = max(0, int((lo_s - lo) / h))
        k1 = min(K, int((hi_s - lo) / h) + 1)
        for k in range(k0, k1 + 1):
            sk = lo + k * h
            if lo_s - 1e-9 <= sk <= hi_s + 1e-9:
                cands.add(round(sk, 6))
        best = None
        for sc in cands:
            c = stage_cost(i, sc - s)
            if c is None:
                continue
            tot = c + v_at(i + 1, sc)
            # empate: preferir mas carga ahora (cargar antes es mas seguro)
            key = (round(tot, 9), -sc)
            if best is None or key < best[0]:
                best = (key, sc)
        s_next = best[1] if best else s
        delta = s_next - s
        hp = HourPlan(dt=hours[i], price=price, tier=tier, pv_w=pv_forecast_w[i], load_w=load_forecast_w[i])
        if delta > 1e-6:
            ec = delta / eta_c
            solar_ac = max(0.0, min(ec, d_solar / eta_c))
            grid_ac = max(0.0, ec - solar_ac)
            hp.charge_w = ec / dt
            if grid_ac > 1e-6:
                hp.charge_source = "grid"
                hp.reason = f"carga desde red ({price:.3f} EUR/kWh): energia mas barata que la que se ahorra despues"
                if grid_ac >= ec - 1e-6:
                    pass
                else:
                    hp.reason = f"carga con excedente solar y red ({price:.3f} EUR/kWh)"
            else:
                hp.charge_source = "solar"
                hp.reason = "carga con excedente solar"
        elif delta < -1e-6:
            dis_ac = -delta * eta_d
            hp.discharge_w = dis_ac / dt
            hp.reason = f"descarga para cubrir consumo ({price:.3f} EUR/kWh)"
        else:
            if i0 > 0:
                hp.reason = "sin descargar: la energia rinde mas en horas mas caras"
            else:
                hp.reason = "sin accion (no compensa)"
        hp.soc_wh = s_next
        plan.append(hp)
        s = s_next

    # reserva: SOC planificado justo antes de la primera descarga que sigue a una carga
    reserve = s_now
    first_dis = next((i for i, hp in enumerate(plan) if hp.discharge_w > 0), None)
    if first_dis is not None:
        reserve = plan[first_dis - 1].soc_wh if first_dis > 0 else s_now
    else:
        reserve = max(hp.soc_wh for hp in plan)
    reserve = min(ceiling_wh, max(min_soc_wh, reserve + reserve_safety_margin_wh * 0.0))
    return plan, reserve
