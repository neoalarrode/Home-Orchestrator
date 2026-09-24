"""Fachadas de consumo INTERNO (device_registry) para el plugin Tuya nuevo.

Mismo contrato que las del plugin viejo (tuya/device_manager.py:
TuyaClimateHandle/TuyaLightHandle) -- las consumen Climate Orchestrator
("climate") y el plugin de iluminacion ("light") EN EL MISMO PROCESO, sin
pasar por HA/MQTT. La diferencia con el viejo: aqui trabajan sobre el modelo
de CODIGOS ESTANDAR (DP codes de la app) y el DeviceController del plugin
nuevo (get_state() -> {code: valor}, set_dp(code, valor)), no sobre objetos
ClimateMapping/LightMapping por modelo.

No son excluyentes con expose_mqtt: el mismo dispositivo puede seguir viendose
como light.*/climate.* en HA (voz, Lovelace) mientras otro plugin lo controla
directo por aqui.
"""
from __future__ import annotations
from typing import Any

from .kits import light as lightkit

# Traduccion del set de instrucciones estandar de Tuya (enum "mode" de kt/qn)
# a modos nativos de HA -- misma correspondencia que ya usaba el plugin viejo
# (cold->cool, hot->heat, wet->dry, wind->fan_only, auto->heat_cool).
TUYA_HVAC_MODE = {
    "cold": "cool", "hot": "heat", "wet": "dry", "wind": "fan_only",
    "auto": "heat_cool", "heat": "heat", "cool": "cool", "dry": "dry",
    "fan": "fan_only", "ventilation": "fan_only",
}
# Codigos estandar candidatos por funcion (se usa el primero que exista en el
# thing-model del dispositivo) -- cubre variantes v1/v2 del set estandar.
_POWER = ("switch", "Power", "switch_1", "power")
_MODE = ("mode",)
_TEMP_SET = ("temp_set", "temp_set_f", "TempSet")
_TEMP_CUR = ("temp_current", "temp_current_f", "upper_temp")
_FAN = ("fan_speed_enum", "windspeed", "fan_speed", "level", "fan")

_LIGHT_SW = ("switch_led", "switch_led_1")
_BRIGHT = ("bright_value_v2", "bright_value")
_TEMP_VAL = ("temp_value_v2", "temp_value")
_COLOUR = ("colour_data_v2", "colour_data")
_WORK_MODE = ("work_mode",)

# Rango fijo de blanco (el DP de Tuya es una escala interna 0..1000, no Kelvin
# real) -- mismo criterio que el plugin viejo.
LIGHT_WHITE_MIN_KELVIN = 2700
LIGHT_WHITE_MAX_KELVIN = 6500


def _first(codes: dict, names) -> str | None:
    """Primer codigo candidato presente en el thing-model. Insensible a
    mayusculas/minusculas y devuelve el nombre REAL del codigo, porque el set
    de instrucciones estandar de Tuya varia el casing por dispositivo: los
    termostatos qn usan `Switch`/`Temp_set`/`Temp_current`/`Mode` capitalizados,
    los AC kt y las bombillas dj usan minusculas (`switch`/`temp_set`/...).
    Verificado contra el thing-model real de ambos en produccion."""
    lower = {k.lower(): k for k in codes}
    for n in names:
        real = lower.get(n.lower())
        if real is not None:
            return real
    return None


def _scale_div(codes: dict, code: str | None) -> float:
    """Divisor de escala de un DP de tipo value: valor real = raw / 10**scale."""
    if not code:
        return 1.0
    spec = (codes.get(code) or {}).get("spec") or {}
    try:
        return 10 ** int(spec.get("scale") or 0)
    except Exception:
        return 1.0


def _enum_range(codes: dict, code: str | None) -> list:
    if not code:
        return []
    spec = (codes.get(code) or {}).get("spec") or {}
    r = spec.get("range")
    return list(r) if isinstance(r, list) else []


class TuyaClimateHandle:
    """`climate` para ZoneRunner -- mismos campos/metodos que lee de un
    climate.* de HA, resueltos contra el DeviceController del plugin nuevo."""

    def __init__(self, controller, codes: dict) -> None:
        self._ctl = controller
        self._codes = codes
        self._sw = _first(codes, _POWER)
        self._mode = _first(codes, _MODE)
        self._tset = _first(codes, _TEMP_SET)
        self._tcur = _first(codes, _TEMP_CUR)
        self._fan = _first(codes, _FAN)

    def _state(self) -> dict:
        try:
            return self._ctl.get_state() or {}
        except Exception:
            return {}

    @property
    def available(self) -> bool:
        return bool(self._state())

    @property
    def current_temperature(self) -> float | None:
        if not self._tcur:
            return None
        raw = self._state().get(self._tcur)
        return raw / _scale_div(self._codes, self._tcur) if raw is not None else None

    @property
    def target_temperature(self) -> float | None:
        if not self._tset:
            return None
        raw = self._state().get(self._tset)
        return raw / _scale_div(self._codes, self._tset) if raw is not None else None

    @property
    def hvac_mode(self) -> str:
        st = self._state()
        if self._sw is not None and not st.get(self._sw):
            return "off"
        if self._mode is not None:
            return TUYA_HVAC_MODE.get(str(st.get(self._mode)), "heat")
        return "heat"

    @property
    def hvac_modes(self) -> list[str]:
        modes: list[str] = []
        if self._sw is not None:
            modes.append("off")
        for v in _enum_range(self._codes, self._mode):
            ha = TUYA_HVAC_MODE.get(str(v))
            if ha and ha not in modes:
                modes.append(ha)
        if not any(m != "off" for m in modes):
            modes.append("heat")
        return modes

    @property
    def fan_mode(self) -> str | None:
        if not self._fan:
            return None
        raw = self._state().get(self._fan)
        return str(raw) if raw is not None else None

    @property
    def fan_modes(self) -> list[str]:
        return [str(v) for v in _enum_range(self._codes, self._fan)]

    def set_temperature(self, value: float) -> None:
        if not self._tset:
            return
        div = _scale_div(self._codes, self._tset)
        self._ctl.set_dp(self._tset, round(value * div) if div != 1 else value)

    def set_hvac_mode(self, hvac_mode: str) -> None:
        if self._sw is not None:
            self._ctl.set_dp(self._sw, hvac_mode != "off")
        if hvac_mode == "off":
            return
        if self._mode is not None:
            reverse = {}
            for v in _enum_range(self._codes, self._mode):
                ha = TUYA_HVAC_MODE.get(str(v))
                if ha and ha not in reverse:
                    reverse[ha] = v
            raw = reverse.get(hvac_mode)
            if raw is not None:
                self._ctl.set_dp(self._mode, raw)

    def set_fan_mode(self, fan_mode: str) -> None:
        if not self._fan:
            return
        # El enum del thing-model puede ser string o numero -- casar contra el rango.
        for v in _enum_range(self._codes, self._fan):
            if str(v) == str(fan_mode):
                self._ctl.set_dp(self._fan, v)
                return


class TuyaLightHandle:
    """`light` para el plugin de iluminacion -- control directo, misma
    conversion de brillo/color que usa la publicacion MQTT (kits/light.py)."""

    def __init__(self, controller, codes: dict) -> None:
        self._ctl = controller
        self._codes = codes
        self._sw = _first(codes, _LIGHT_SW)
        self._bright = _first(codes, _BRIGHT)
        self._ctemp = _first(codes, _TEMP_VAL)
        self._colour = _first(codes, _COLOUR)
        self._work = _first(codes, _WORK_MODE)

    def _state(self) -> dict:
        try:
            return self._ctl.get_state() or {}
        except Exception:
            return {}

    @property
    def available(self) -> bool:
        return bool(self._state())

    @property
    def is_on(self) -> bool:
        return bool(self._state().get(self._sw)) if self._sw else False

    @property
    def brightness_pct(self) -> float | None:
        if not self._bright:
            return None
        raw = self._state().get(self._bright)
        if raw is None:
            return None
        return round(lightkit.brightness_to_ha(int(raw)) * 100 / 255, 1)

    @property
    def color_temp_kelvin(self) -> int | None:
        if not self._ctemp:
            return None
        raw = self._state().get(self._ctemp)
        if raw is None:
            return None
        # DP interno 0..1000 (0=calido) -> Kelvin del rango fijo.
        frac = max(0, min(1000, int(raw))) / 1000
        return round(LIGHT_WHITE_MIN_KELVIN + frac * (LIGHT_WHITE_MAX_KELVIN - LIGHT_WHITE_MIN_KELVIN))

    @property
    def color_temp_range(self) -> tuple[int, int] | None:
        if not self._ctemp:
            return None
        return (LIGHT_WHITE_MIN_KELVIN, LIGHT_WHITE_MAX_KELVIN)

    def turn_on(self, brightness_pct: float | None = None, color_temp_kelvin: float | None = None,
                hs: tuple[float, float] | None = None) -> None:
        if self._sw:
            self._ctl.set_dp(self._sw, True)
        if hs is not None and self._colour:
            if self._work:
                self._ctl.set_dp(self._work, "colour")
            v = brightness_pct if brightness_pct is not None else 100.0
            self._ctl.set_dp(self._colour, lightkit.encode_colour(int(hs[0]), int(hs[1]), int(v)))
            return
        if self._work and (brightness_pct is not None or color_temp_kelvin is not None):
            self._ctl.set_dp(self._work, "white")
        if brightness_pct is not None and self._bright:
            ha = round(max(0.0, min(100.0, brightness_pct)) * 255 / 100)
            self._ctl.set_dp(self._bright, lightkit.brightness_from_ha(ha))
        if color_temp_kelvin is not None and self._ctemp and color_temp_kelvin > 0:
            k = max(LIGHT_WHITE_MIN_KELVIN, min(LIGHT_WHITE_MAX_KELVIN, color_temp_kelvin))
            frac = (k - LIGHT_WHITE_MIN_KELVIN) / (LIGHT_WHITE_MAX_KELVIN - LIGHT_WHITE_MIN_KELVIN)
            self._ctl.set_dp(self._ctemp, round(frac * 1000))

    def turn_off(self) -> None:
        if self._sw:
            self._ctl.set_dp(self._sw, False)
