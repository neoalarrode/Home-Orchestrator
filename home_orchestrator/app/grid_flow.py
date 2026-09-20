"""
Balance de flujos de energia de la casa en un instante, en una funcion
pura -- la MISMA para `run_cycle` (que alimenta los acumulados), `/api/live`
(dashboard) y las simulaciones. Antes la formula estaba copiada en dos
sitios y habia ido divergiendo.

Convenio: todas las potencias en W y >= 0 salvo `net_grid_w` (con signo:
positivo importando, negativo vertiendo).
"""

from __future__ import annotations


def flows(load_w: float, pv_w: float, charge_w: float, discharge_w: float,
          net_grid_w: float | None = None) -> dict:
    """Reparte solar/bateria/red. Con `net_grid_w` (medidor de red real, modo
    "combined") la importacion ES esa lectura, sin reconstruir nada."""
    solar_to_casa_w = min(pv_w, load_w)
    solar_surplus_w = max(0.0, pv_w - load_w)
    solar_to_batt_w = min(solar_surplus_w, charge_w)
    grid_to_batt_w = max(0.0, charge_w - solar_to_batt_w)
    batt_to_casa_w = discharge_w
    grid_to_casa_w = max(0.0, load_w - solar_to_casa_w - batt_to_casa_w)
    grid_total_w = grid_to_casa_w + grid_to_batt_w
    if net_grid_w is not None:
        grid_total_w = max(0.0, net_grid_w)
    return {
        "solar_to_casa_w": solar_to_casa_w,
        "solar_to_batt_w": solar_to_batt_w,
        "batt_to_casa_w": batt_to_casa_w,
        "grid_to_batt_w": grid_to_batt_w,
        "grid_to_casa_w": grid_to_casa_w,
        "grid_total_w": grid_total_w,
    }


def import_for_accumulation(*, net_grid_w: float | None, flow_grid_total_w: float,
                            load_measured: bool, pv_measured: bool,
                            battery_measured: bool) -> float | None:
    """Potencia de importacion que puede ENTRAR en un contador acumulado, o
    `None` si no hay base medida para ella.

    BUG REAL, medido contra un Shelly Pro 3EM: `run_cycle` rellenaba los
    datos que faltaban con la PREVISION del planificador (consumo, solar,
    carga de bateria) y el resultado se integraba en un contador
    `total_increasing` como si fuera una medida. Un contador acumulado solo
    debe crecer con medidas; sin ellas se pasa `None` (`grid_energy_store.
    accumulate` ya lo trata como "no tocar el contador").

    - Con medidor de red real (`net_grid_w`): esa lectura, siempre.
    - Sin el: solo si consumo, solar y bateria son medidas de este instante.
    """
    if net_grid_w is not None:
        return max(0.0, net_grid_w)
    if load_measured and pv_measured and battery_measured:
        return flow_grid_total_w
    return None


def accumulate_grid(store, now, *, imp_declared: bool, exp_declared: bool,
                    imp_counter_kwh: float | None, exp_counter_kwh: float | None,
                    imp_power_w: float | None, exp_power_w: float | None) -> dict:
    """Un paso del acumulado de red: la MISMA funcion la usan `run_cycle` y
    las pruebas, para que lo que se prueba sea lo que corre.

    - Direccion con contador de energia DECLARADO: solo sale de sus
      incrementos. Si esta ciclo no responde (`*_counter_kwh` None) no se
      integra potencia: el contador recupera el hueco al volver, e integrarlo
      tambien lo contaria dos veces.
    - Direccion sin contador: se integra potencia (None = no tocar).
    """
    no_declaradas = [c for c, d in (("imported_kwh", imp_declared), ("exported_kwh", exp_declared)) if not d]
    if no_declaradas:
        store.forget_counters(no_declaradas)
    totals = store.accumulate(
        now,
        None if imp_declared else imp_power_w,
        None if exp_declared else exp_power_w,
    )
    if imp_counter_kwh is not None or exp_counter_kwh is not None:
        totals = store.from_counters(imp_counter_kwh, exp_counter_kwh, now)
    return totals


def shared_solar(cfg: dict) -> bool:
    """True si algun array es de AUTOCONSUMO COMPARTIDO (cuota < 100 %): la
    unica situacion en que el importado NO es lo que mide el medidor."""
    for a in (cfg.get("pv_arrays") or []):
        v = a.get("self_consumption_share_pct")
        try:
            if (100.0 if v is None else float(v)) < 100.0:
                return True
        except (TypeError, ValueError):
            continue
    return False
