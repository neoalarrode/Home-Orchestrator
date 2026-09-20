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
