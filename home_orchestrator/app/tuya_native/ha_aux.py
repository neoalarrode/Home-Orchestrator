from __future__ import annotations
UNIT_DC={"℃":"temperature","°C":"temperature","W":"power","kWh":"energy","V":"voltage","A":"current","lux":"illuminance"}
def build_aux(e,device_id,name,bt):
    code=e["code"]; dom=e["domain"]; st=bt+"/state"; cmd=bt+"/cmd/"+code
    dev={"identifiers":["tuya_%s"%device_id],"name":name,"manufacturer":"Tuya"}
    cfg={"name":code,"unique_id":"tuya_%s_%s"%(device_id,code),"device":dev,"availability_topic":bt+"/avail","state_topic":st,"value_template":"{{ value_json.%s }}"%code}
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
        if e.get("unit"): cfg["unit_of_measurement"]=e["unit"]
        if scale: cfg["value_template"]="{{ value_json.%s | float / %d }}"%(code,d)
    elif dom=="sensor":
        if e.get("unit"):
            cfg["unit_of_measurement"]=e["unit"]; dc=UNIT_DC.get(e["unit"])
            if dc: cfg["device_class"]=dc
        if scale: cfg["value_template"]="{{ value_json.%s | float / %d }}"%(code,d)
    return cfg
