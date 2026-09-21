"""
Decision de un ciclo del planificador de baterias, como FUNCION PURA.

Antes esta logica vivia inline en `main.run_cycle` (mezclada con lecturas a
Home Assistant, publicacion de sensores y acumulados). Se extrae aqui tal
cual, sin cambiar comportamiento, para poder ejecutar EXACTAMENTE el mismo
codigo en pruebas y simulaciones con reloj acelerado, sin Flask ni HA.

Entradas: previsiones ya calculadas (solar, consumo, precios), el SOC medido
de cada bateria y la configuracion general. Salidas: el plan hora a hora, la
reserva objetivo y los agregados que `run_cycle` necesita para publicar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import logging
import math

import scheduler
import scheduler_dp


log = logging.getLogger(__name__)
DEFAULT_PLANNER = "classic"


@dataclass
class CycleDecision:
    plan: list
    reserve_wh: float
    total_capacity_wh: float
    current_soc_wh: float
    current_soc_pct: float
    min_soc_wh: float
    max_charge_w: float
    max_discharge_w: float
    max_usable_wh: float


def decide(
    now: datetime,
    general_cfg: dict,
    usable_batteries: list,
    socs: dict,
    pv_forecast: list[float],
    load_forecast: list[float],
    prices_tiers: list[tuple[float, str]],
) -> CycleDecision:
    """`usable_batteries`: solo las que tienen SOC disponible este ciclo."""
    total_capacity_wh = sum(b.capacity_wh for b in usable_batteries)
    current_soc_wh = sum(socs[b.id] / 100 * b.capacity_wh for b in usable_batteries)
    # SOC real AHORA MISMO, medido (no la proyeccion de hp.soc_wh del plan).
    current_soc_pct = round(100 * current_soc_wh / total_capacity_wh, 1) if total_capacity_wh else 0
    min_soc_wh = sum(b.min_soc_pct / 100 * b.capacity_wh for b in usable_batteries)
    max_charge_w = sum(b.max_charge_w for b in usable_batteries)
    max_discharge_w = sum(b.max_discharge_w for b in usable_batteries)
    # techo real de carga (SOC maximo declarado por bateria, no el 100% nominal)
    max_usable_wh = sum(b.max_soc_pct / 100 * b.capacity_wh for b in usable_batteries)

    # Prioridad elegida por el usuario: "ahorro" (carga tambien desde red si
    # hace falta), "autoconsumo" (solo excedente solar) o "longevidad" (como
    # "ahorro" pero sin apurar el SOC objetivo por encima del 90%).
    priority_mode = general_cfg.get("priority_mode", "ahorro")
    allow_grid_charging = priority_mode != "autoconsumo"
    paced_charging = bool(general_cfg.get("paced_charging", False)) and allow_grid_charging
    effective_max_usable_wh = max_usable_wh
    if priority_mode == "longevidad" and total_capacity_wh:
        effective_max_usable_wh = min(max_usable_wh, total_capacity_wh * 0.90)

    # Colchon de seguridad: % de la capacidad util (max_usable - min_soc).
    reserve_safety_margin_pct = float(general_cfg.get("reserve_safety_margin_pct") or 0)
    usable_capacity_wh = max(0.0, effective_max_usable_wh - min_soc_wh)
    reserve_safety_margin_wh = usable_capacity_wh * reserve_safety_margin_pct / 100

    common = dict(
        now=now,
        pv_forecast_w=pv_forecast,
        load_forecast_w=load_forecast,
        current_soc_wh=current_soc_wh,
        total_capacity_wh=total_capacity_wh,
        max_charge_w=max_charge_w,
        max_discharge_w=max_discharge_w,
        min_soc_wh=min_soc_wh,
        prices_tiers=prices_tiers,
        contracted_power_w=float(general_cfg.get("contracted_power_w") or 0),
        max_usable_wh=effective_max_usable_wh,
        allow_grid_charging=allow_grid_charging,
        paced_charging=paced_charging,
        reserve_safety_margin_wh=reserve_safety_margin_wh,
    )
    if general_cfg.get("planner", DEFAULT_PLANNER) == "dp":
        try:
            rt = float(general_cfg.get("battery_roundtrip_efficiency_pct") or 88) / 100
            eta = math.sqrt(min(1.0, max(0.5, rt)))
            plan, reserve_wh = scheduler_dp.build_plan_dp(
                **common, eta_charge=eta, eta_discharge=eta,
                export_price=float(general_cfg.get("export_price_eur_kwh", 0.04) or 0),
                wear_eur_per_kwh=float(general_cfg.get("battery_wear_eur_kwh", 0.01) or 0),
                load_margin_frac=float(general_cfg.get("dp_load_margin_frac", 0.0)),
                pv_margin_frac=float(general_cfg.get("dp_pv_margin_frac", 0.0)),
            )
        except Exception:
            log.warning("El motor 'dp' fallo; se usa el planificador 'classic' en este ciclo", exc_info=True)
            plan, reserve_wh = scheduler.build_plan(**common)
    else:
        plan, reserve_wh = scheduler.build_plan(**common)
    return CycleDecision(
        plan=plan, reserve_wh=reserve_wh, total_capacity_wh=total_capacity_wh,
        current_soc_wh=current_soc_wh, current_soc_pct=current_soc_pct, min_soc_wh=min_soc_wh,
        max_charge_w=max_charge_w, max_discharge_w=max_discharge_w, max_usable_wh=max_usable_wh,
    )


def ac_charge_for_now(now_hp, hybrid_pv_now_w: float) -> float:
    """Lo que ya se autoconsume directo en paneles 'hybrid' no hay que
    mandarlo otra vez por AC: se descuenta de la carga a ordenar."""
    ac_charge_w = now_hp.charge_w
    if now_hp.charge_source == "solar":
        ac_charge_w = max(0.0, now_hp.charge_w - hybrid_pv_now_w)
    return ac_charge_w


def cap_grid_charge_for_contract(ac_charge_w: float, charge_source: str | None, contracted_power_w: float,
                                 grid_import_w: float | None, live_charge_w: float | None,
                                 margin: float = 0.95) -> float:
    """Limita la carga DESDE RED con la importacion medida AHORA: el plan solo
    conoce la media horaria del consumo, y un pico real (horno + aire) sumado a
    la carga de las baterias superaba la potencia contratada (simulacion: 1-3
    min/dia). base = importacion medida sin la carga actual de las baterias."""
    if charge_source != "grid" or not contracted_power_w or contracted_power_w <= 0 or grid_import_w is None:
        return ac_charge_w
    base = max(0.0, grid_import_w - max(0.0, live_charge_w or 0.0))
    return min(ac_charge_w, max(0.0, contracted_power_w * margin - base))
