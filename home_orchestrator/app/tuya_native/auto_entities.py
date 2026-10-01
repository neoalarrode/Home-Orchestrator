"""Auto-mapeo GENÉRICO de entidades HA desde el thing-model, SIN tablas por dispositivo
ni DPs a mano. La única fuente es el thing-model (code/type/spec), que es lo mismo que
usa el panel de la app para construir su modelo (__dpSchema__ = devInfo.schema.reduce).

Reglas universales (estándar Tuya, iguales para TODOS los productos):
- El DOMINIO PRINCIPAL se detecta por los CÓDIGOS ESTÁNDAR presentes, no por una tabla
  categoría->dominio. (temp_set+mode/temp_current -> climate; bright/colour -> light; etc.)
- Cada DP se expone como entidad por su TIPO, con TODAS sus características derivadas del
  spec (min/max/scale/step/unit/range/accessMode/icon). Nada hardcodeado por dispositivo.
Las entidades compuestas (climate/light/cover/fan/vacuum) reutilizan los builders ha_*,
que ya derivan sus características del spec y usan solo códigos estándar.
"""
from __future__ import annotations
from typing import Dict, Any, List
from . import ha_climate, ha_vacuum, ha_light, ha_cover, ha_fan

# Conjuntos de códigos ESTÁNDAR Tuya (universales) para detectar el dominio principal.
# No es mapeo por dispositivo: es el "standard instruction set" de Tuya, uno para todos.
def _hasany(codes, names): return any(n in codes for n in names)

CLIMATE_SET = {"temp_set","Temp_set","temp_set_f"}
CLIMATE_SUP = {"temp_current","Temp_current","mode","Mode","windspeed","humidity_current"}
LIGHT_SET   = {"bright_value","bright_value_v2","colour_data","colour_data_v2","temp_value","temp_value_v2","switch_led","switch_led_1"}
COVER_SET   = {"control","percent_control","position","mach_operate","control_2","percent_control_2"}
VACUUM_SET  = {"switch_go","mode_vacuum","suction","power_go","seek","direction_control"}
FAN_SET     = {"fan_speed","fan_speed_enum","fan_speed_percent"}
SWITCHY     = ("switch","switch_1","Switch","start")

# DPs puramente "ruido" de protocolo (no entidades). Universal, no por dispositivo.
NOISE = {"markbit","command_trans","request","response","net_notify","report","device_info",
         "work_time","run_time","style","boolCode","map_data","path_data"}

def detect_main(category: str, codes: Dict[str, Any]) -> str:
    """Dominio principal por códigos estándar presentes (category solo como desempate)."""
    if _hasany(codes, CLIMATE_SET) and _hasany(codes, CLIMATE_SUP): return "climate"
    if _hasany(codes, VACUUM_SET) or category == "sd": return "vacuum"
    if _hasany(codes, LIGHT_SET): return "light"
    if _hasany(codes, COVER_SET): return "cover"
    # fan: ventilador si hay velocidad de ventilador Y no es climate
    if _hasany(codes, FAN_SET) and not _hasany(codes, CLIMATE_SET): return "fan"
    if any(c.startswith("switch") or c in SWITCHY for c in codes): return "switch"
    return "sensor"

def _ro(sp):
    am = str((sp or {}).get("accessMode") or "")
    return bool(am) and "w" not in am

def dp_entity(code: str, info: Dict[str, Any]) -> Dict[str, Any] | None:
    """Entidad genérica por TIPO de DP, con TODAS las características del spec."""
    typ = info.get("type"); sp = info.get("spec") or {}; ro = _ro(sp)
    ext = sp.get("extensions") if isinstance(sp.get("extensions"), dict) else {}
    icon = (ext or {}).get("iconName")
    base = {"code": code, "dp": info.get("dp"), "icon": icon, "read_only": ro}
    if typ == "bool":
        return {**base, "domain": "binary_sensor" if ro else "switch"}
    if typ == "enum":
        return {**base, "domain": "sensor" if ro else "select", "options": sp.get("range") or []}
    if typ in ("value", "integer"):
        return {**base, "domain": "sensor" if ro else "number",
                "min": sp.get("min"), "max": sp.get("max"), "scale": sp.get("scale", 0),
                "step": sp.get("step"), "unit": sp.get("unit")}
    if typ in ("string", "json", "raw", "bitmap"):
        return {**base, "domain": "sensor"} if ro else None
    return None

# Códigos ya "consumidos" por cada entidad compuesta (para no duplicarlos como DP suelto).
_MAIN_ROLES = {
    "climate": lambda c: set(ha_climate.CLIMATE_MAINCODES) & set(c),
    "light":   lambda c: {x for x in c if x in LIGHT_SET or x.startswith("switch_led")},
    "cover":   lambda c: {x for x in c if x in COVER_SET},
    "fan":     lambda c: {x for x in c if x in FAN_SET or x in SWITCHY},
    "vacuum":  lambda c: {x for x in c if x in getattr(ha_vacuum, "VACUUM_ROLES", set())} | (VACUUM_SET & set(c)),
}

def build_entities_auto(category: str, codes: Dict[str, Any], name: str, device_id: str,
                        base_topic: str = "tuya_native") -> Dict[str, Any]:
    bt = "%s/%s" % (base_topic, device_id)
    main = detect_main(category, codes)
    ents: List[Dict[str, Any]] = []; consumed: set = set()
    if main == "climate":
        ents.append({"domain": "climate", "role": "main", "config": ha_climate.build_climate(device_id, name, codes, bt)})
    elif main == "vacuum":
        fs = ((codes.get("suction", {}) or {}).get("spec", {}) or {}).get("range")
        ents.append({"domain": "vacuum", "role": "main", "config": ha_vacuum.build_vacuum(device_id, name, codes, bt, fs)})
    elif main == "light":
        ents.append({"domain": "light", "role": "main", "config": ha_light.build_light(device_id, name, codes, bt)})
    elif main == "cover":
        ents.append({"domain": "cover", "role": "main", "config": ha_cover.build_cover(device_id, name, codes, bt)})
    elif main == "fan":
        ents.append({"domain": "fan", "role": "main", "config": ha_fan.build_fan(device_id, name, codes, bt)})
    elif main == "switch":
        for c in [x for x in codes if x.startswith("switch") or x in SWITCHY]:
            ents.append({"domain": "switch", "role": "main", "code": c, "dp": codes[c].get("dp")}); consumed.add(c)
    consumed |= _MAIN_ROLES.get(main, lambda c: set())(codes)
    # EXPONER TODOS los demás DPs automáticamente (nada gated, nada a mano)
    for code, info in codes.items():
        if code in consumed or code in NOISE: continue
        e = dp_entity(code, info)
        if e: ents.append(e); consumed.add(code)
    return {"category": category, "main_domain": main, "n_entities": len(ents), "entities": ents}
