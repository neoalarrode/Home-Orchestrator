"""
Potencia de climatizacion PREVISTA de una zona, hora a hora -- para que Energy
planifique con lo que Climate va a gastar, no solo con la media de otros dias
(decision del usuario, 4-nov: una noche mas fria que la semana pasada dejaba a
Energy con menos reserva de la necesaria).

No es "toda la hora a plena potencia o nada" (eso es la grafica de la zona):
con el modelo termico ya aprendido de la habitacion,

  - por debajo de la consigna: el tiempo a plena potencia que tarda en llegar
    (`(consigna - temp) / ritmo_de_calentar`, como mucho la hora entera);
  - en la consigna: la fraccion de hora que hace falta para reponer lo que se
    pierde hacia el exterior (`perdidas * (consigna - exterior) / ritmo`),

y lo mismo en frio. Esa fraccion por la potencia de la zona son sus vatios de
esa hora. Sin modelo fiable o sin potencia conocida no hay previsión (None):
quien la use tiene que saber que NO la hay, nunca un cero inventado.
"""

from __future__ import annotations


def project(start_temp: float | None, outdoor: list[float], targets: list[tuple[float | None, float | None]],
            heating_rate: float, cooling_rate: float, idle_loss: float | None,
            power_w) -> list[float] | None:
    """Vatios previstos para cada hora de `targets` ((consigna_calor,
    consigna_frio) por hora; None = sin consigna en ese sentido). `outdoor[i]`
    es el exterior previsto esa hora (se repite el ultimo si falta).
    `power_w`: vatios de la zona, o una funcion (modo, exterior) -> vatios
    (la bomba de calor gasta distinto en frio que en calor y segun el exterior)."""
    if start_temp is None or idle_loss is None or not outdoor:
        return None
    if not callable(power_w):
        if not power_w or power_w <= 0:
            return None
        fixed = float(power_w)
        power_w = lambda mode, ext: fixed
    temp = start_temp
    out: list[float] = []
    for i, (heat_t, cool_t) in enumerate(targets):
        ext = outdoor[i] if i < len(outdoor) else outdoor[-1]
        fraction = 0.0
        # Hace falta calentar o enfriar pero la zona aun no sabe a que ritmo
        # (p.ej. el primer dia de aire en verano): no hay previsión, mejor que
        # un 0 que dejaria a Energy con menos reserva de la necesaria.
        if (heat_t is not None and heating_rate <= 0 and (temp < heat_t or (temp <= heat_t and ext < heat_t))) or \
                (cool_t is not None and cooling_rate <= 0 and (temp > cool_t or (temp >= cool_t and ext > cool_t))):
            return None
        mode = None
        if heat_t is not None and temp <= heat_t and heating_rate > 0:
            mode = "heat"
            reach = min(1.0, max(0.0, heat_t - temp) / heating_rate)
            hold = max(0.0, idle_loss * (heat_t - ext) / heating_rate) if heat_t > ext else 0.0
            fraction = reach + (1.0 - reach) * min(1.0, hold)
            temp = min(heat_t, temp + heating_rate) if reach >= 1.0 else heat_t
        elif cool_t is not None and temp >= cool_t and cooling_rate > 0:
            mode = "cool"
            reach = min(1.0, max(0.0, temp - cool_t) / cooling_rate)
            hold = max(0.0, idle_loss * (ext - cool_t) / cooling_rate) if ext > cool_t else 0.0
            fraction = reach + (1.0 - reach) * min(1.0, hold)
            temp = max(cool_t, temp - cooling_rate) if reach >= 1.0 else cool_t
        else:
            # Sin pedir nada: deriva hacia el exterior con sus perdidas.
            temp = temp + idle_loss * (ext - temp)
        if fraction <= 0:
            out.append(0.0)
            continue
        watts = power_w(mode, ext)
        if not watts or watts <= 0:
            return None                      # hace falta y no se sabe cuanto gasta
        out.append(round(min(1.0, fraction) * watts, 1))
    return out
