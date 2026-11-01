from __future__ import annotations
UNIT_DC={"℃":"temperature","°C":"temperature","W":"power","kWh":"energy","V":"voltage","A":"current","lux":"illuminance"}
def _tpl(code, expr, dom):
    """`expr` si el JSON trae `code`; si no, el valor que ya tenia la entidad
    (`this`, disponible en las plantillas MQTT de entrada). BUG REAL (1-nov): el
    estado solo trae los DP que el equipo ha informado y el modelo declara mas
    (radiadores: anion, windshake, C_F...; robot: y_mop, uv...) -- cada
    publicacion dejaba un "Template variable warning" por DP ausente en el log
    de HA, ~4.000 lineas/hora. "None" es el PAYLOAD_NONE de MQTT: desconocido."""
    if dom in ("switch", "binary_sensor"):
        keep = "('True' if this.state == 'on' else ('False' if this.state == 'off' else 'None'))"
    else:
        keep = "(this.state if this.state not in ('unknown', 'unavailable') else 'None')"
    return "{{ (%s) if '%s' in value_json else %s }}" % (expr, code, keep)


def build_aux(e,device_id,name,bt):
    code=e["code"]; dom=e["domain"]; st=bt+"/state"; cmd=bt+"/cmd/"+code
    dev={"identifiers":["tuya_%s"%device_id],"name":name,"manufacturer":"Tuya"}
    cfg={"name":code,"unique_id":"tuya_%s_%s"%(device_id,code),"device":dev,"availability_topic":bt+"/avail","state_topic":st,"value_template":_tpl(code,"value_json['%s']"%code,dom)}
    if e.get("icon"): cfg["icon"]="mdi:"+str(e["icon"]).replace("icon-dp_","").replace("_","-")
    scale=int(e.get("scale") or 0); d=10**scale
    # El estado crudo publica el bool como true/false JSON; value_template lo
    # renderiza como "True"/"False" (Jinja) -> state_on/off deben casar ese texto.
    if dom=="switch": cfg.update({"command_topic":cmd,"payload_on":"true","payload_off":"false","state_on":"True","state_off":"False"})
    elif dom=="binary_sensor": cfg.update({"payload_on":True,"payload_off":False})
    elif dom=="select": cfg.update({"command_topic":cmd,"options":e.get("options") or []})
    elif dom=="number":
        cfg["command_topic"]=cmd
        if e.get("min") is not None: cfg["min"]=e["min"]/d
        if e.get("max") is not None: cfg["max"]=e["max"]/d
        if e.get("unit"): cfg["unit_of_measurement"]={"℃":"°C","℉":"°F"}.get(e["unit"],e["unit"])
        if scale: cfg["value_template"]=_tpl(code,"value_json['%s'] | float / %d"%(code,d),dom)
    elif dom=="sensor":
        if e.get("unit"):
            # HA rechaza "℃" (U+2103) con device_class temperature: la entidad no
            # se creaba (sensor de temperatura del robot, 1-nov).
            unit={"℃":"°C","℉":"°F"}.get(e["unit"],e["unit"])
            cfg["unit_of_measurement"]=unit; dc=UNIT_DC.get(unit)
            if dc: cfg["device_class"]=dc
        if scale: cfg["value_template"]=_tpl(code,"value_json['%s'] | float / %d"%(code,d),dom)
    return cfg
