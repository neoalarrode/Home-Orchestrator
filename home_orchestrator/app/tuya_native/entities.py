"""Generador central: (categoria + DPs) -> entidades HA nativas. Visibilidad por
valor real; opcion 'controles avanzados' opt-in."""
from __future__ import annotations
from typing import Any, Dict, List
from . import ha_climate, ha_vacuum, ha_light, ha_cover, ha_fan
CATEGORY_MAIN={"kt":"climate","qn":"climate","wk":"climate","rs":"climate","dj":"light","dd":"light","dc":"light","xdd":"light","fwd":"light","tgq":"light","tyndj":"light","cz":"switch","pc":"switch","kg":"switch","tdq":"switch","wkf":"switch","sd":"vacuum","cl":"cover","clkg":"cover","mc":"cover","fs":"fan","kj":"fan","cs":"fan","ggq":"switch","sfkzq":"switch","msp":"switch","wsdcg":"sensor","mcs":"binary_sensor","pir":"binary_sensor"}
COVER_ROLE={"control","percent_control","position"}; FAN_ROLE={"switch","fan_switch","fan_speed","speed","mode"}
SWITCH_MAIN={"switch","switch_1","Switch","start"}
NOISE_CODES={"markbit","boolCode","command_trans","total_error","fault2","request","response","net_notify","report","device_info","work_time","run_time","style"}
def _ro(sp): am=str(sp.get("accessMode") or ""); return bool(am) and "w" not in am
def _dp_entity(code,info):
    typ=info.get("type"); sp=info.get("spec") or {}; ro=_ro(sp)
    icon=(sp.get("extensions") or {}).get("iconName") if isinstance(sp.get("extensions"),dict) else None
    base={"code":code,"dp":info.get("dp"),"icon":icon}
    if typ=="bool": return {**base,"domain":"binary_sensor" if ro else "switch"}
    if typ=="enum": return {**base,"domain":"sensor" if ro else "select","options":sp.get("range") or []}
    if typ in ("value","integer"): return {**base,"domain":"sensor" if ro else "number","min":sp.get("min"),"max":sp.get("max"),"scale":sp.get("scale",0),"step":sp.get("step"),"unit":sp.get("unit")}
    if typ in ("string","json","raw","bitmap"): return {**base,"domain":"sensor"} if ro else None
    return None
def build_entities(category,codes,name,device_id,base_topic="tuya_native",state=None,expose_advanced=False):
    bt="%s/%s"%(base_topic,device_id); main=CATEGORY_MAIN.get(category,"switch"); ents=[]; consumed=set()
    if main=="climate":
        ents.append({"domain":"climate","role":"main","config":ha_climate.build_climate(device_id,name,codes,bt)}); consumed|={c for c in codes if c in ha_climate.CLIMATE_MAINCODES}
    elif main=="vacuum":
        fs=(codes.get("suction",{}).get("spec",{}) or {}).get("range"); ents.append({"domain":"vacuum","role":"main","config":ha_vacuum.build_vacuum(device_id,name,codes,bt,fs)}); consumed|={c for c in codes if c in ha_vacuum.VACUUM_ROLES}
    elif main=="light":
        from .kits.light import LIGHT_ROLE; ents.append({"domain":"light","role":"main","config":ha_light.build_light(device_id,name,codes,bt)}); consumed|={c for c in codes if c in LIGHT_ROLE}
    elif main=="cover":
        ents.append({"domain":"cover","role":"main","config":ha_cover.build_cover(device_id,name,codes,bt)}); consumed|={c for c in codes if c in COVER_ROLE}
    elif main=="fan":
        ents.append({"domain":"fan","role":"main","config":ha_fan.build_fan(device_id,name,codes,bt)}); consumed|={c for c in codes if c in FAN_ROLE}
    elif main=="switch":
        for c in [x for x in codes if x.startswith("switch") or x in SWITCH_MAIN]:
            ents.append({"domain":"switch","role":"main","code":c,"dp":codes[c].get("dp")}); consumed.add(c)
    def active(code):
        if state is None: return True
        v=state.get(code); return v is not None and v not in ("unavailable","unknown","")
    if expose_advanced:
        for code,info in codes.items():
            if code in consumed or code in NOISE_CODES or not active(code): continue
            e=_dp_entity(code,info)
            if e: ents.append(e)
    return {"category":category,"main_domain":main,"n_entities":len(ents),"entities":ents,"expose_advanced":expose_advanced}
