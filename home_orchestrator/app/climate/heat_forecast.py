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


class Calibrator:
    """Corrige la previsión de una zona con lo que de verdad gasta (4-nov: la
    previsión cruda daba 29 kWh/dia con 9 reales -- un inverter en la consigna
    modula y gasta mucho menos que su potencia de arranque). Compara, hora a
    hora, lo previsto al empezar la hora con lo gastado en ella, y aprende un
    factor (con memoria que se va olvidando). Hasta MIN_HOURS horas comparadas
    no da el visto bueno: la zona no publica previsión y Energy sigue con su
    media historica."""

    MIN_HOURS = 24
    DECAY_PER_HOUR = 1 / 72          # ~3 dias de memoria
    MIN_FORECAST_WH = 1500.0         # por debajo, lo previsto es casi nada: factor 1
    FACTOR_RANGE = (0.1, 2.0)
    MAX_GAP_S = 900

    def __init__(self, state: dict | None = None):
        st = state if isinstance(state, dict) else {}
        self.pred: dict[str, float] = dict(st.get("pred") or {})
        self.real: dict[str, float] = dict(st.get("real") or {})
        self.sum_pred = float(st.get("sum_pred") or 0.0)
        self.sum_real = float(st.get("sum_real") or 0.0)
        self.hours = int(st.get("hours") or 0)
        self._last_ts: float | None = None

    def to_state(self) -> dict:
        return {"pred": self.pred, "real": self.real, "sum_pred": round(self.sum_pred, 1),
                "sum_real": round(self.sum_real, 1), "hours": self.hours}

    def note_forecast(self, hour_key: str, watts_this_hour: float) -> None:
        """Lo previsto para la hora `hour_key` (iso de la hora en punto) al
        empezarla: solo cuenta la primera vez."""
        self.pred.setdefault(hour_key, float(watts_this_hour))

    def add_energy(self, hour_key: str, now_ts: float, watts: float | None) -> None:
        """Integra la potencia de la zona AHORA desde la ultima llamada."""
        if self._last_ts is not None and watts is not None:
            dt = min(max(0.0, now_ts - self._last_ts), self.MAX_GAP_S)
            self.real[hour_key] = self.real.get(hour_key, 0.0) + float(watts) * dt / 3600.0
        self._last_ts = now_ts

    def close_hours(self, current_hour_key: str) -> None:
        """Pasa a la estadistica las horas ya terminadas con las dos medidas."""
        for key in sorted(set(self.pred) | set(self.real)):
            if key >= current_hour_key:
                continue
            if key in self.pred and key in self.real:
                keep = 1.0 - self.DECAY_PER_HOUR
                self.sum_pred = self.sum_pred * keep + self.pred[key]
                self.sum_real = self.sum_real * keep + self.real[key]
                self.hours += 1
            self.pred.pop(key, None)
            self.real.pop(key, None)

    def factor(self) -> float | None:
        if self.hours < self.MIN_HOURS:
            return None
        if self.sum_pred < self.MIN_FORECAST_WH:
            return 1.0
        lo, hi = self.FACTOR_RANGE
        return min(hi, max(lo, self.sum_real / self.sum_pred))
