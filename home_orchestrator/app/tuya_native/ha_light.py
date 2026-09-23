from __future__ import annotations
def build_light(device_id,name,codes,bt):
    def has(*c): return any(x in codes for x in c)
    st=bt+"/state"; cmd=bt+"/set"
    cfg={"schema":"json","name":name,"unique_id":"tuya_%s_light"%device_id,"device":{"identifiers":["tuya_%s"%device_id],"name":name,"manufacturer":"Tuya"},"availability_topic":bt+"/avail","state_topic":st,"command_topic":cmd}
    if has("bright_value","bright_value_v2"): cfg["brightness"]=True; cfg["brightness_scale"]=255
    if has("temp_value","temp_value_v2"): cfg["color_temp"]=True
    if has("colour_data","colour_data_v2"): cfg["supported_color_modes"]=["hs"]+(["color_temp"] if cfg.get("color_temp") else [])
    elif cfg.get("color_temp"): cfg["supported_color_modes"]=["color_temp"]
    elif cfg.get("brightness"): cfg["supported_color_modes"]=["brightness"]
    if has("work_mode","scene_data","scene_data_v2"):
        cfg["effect"]=True; cfg["effect_list"]=((codes.get("work_mode",{}).get("spec",{}) or {}).get("range") or ["white","colour","scene","music"])
    return cfg
