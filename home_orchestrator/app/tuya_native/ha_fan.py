from __future__ import annotations
def build_fan(device_id,name,codes,bt):
    st=bt+"/state"; cmd=bt+"/cmd"
    on="fan_switch" if "fan_switch" in codes else "switch"
    cfg={"name":name,"unique_id":"tuya_%s_fan"%device_id,"device":{"identifiers":["tuya_%s"%device_id],"name":name,"manufacturer":"Tuya"},"availability_topic":bt+"/avail","state_topic":st,"state_value_template":"{{ 'ON' if value_json.%s else 'OFF' }}"%on,"command_topic":cmd+"/"+on,"payload_on":"true","payload_off":"false"}
    spd="fan_speed" if "fan_speed" in codes else ("speed" if "speed" in codes else None)
    if spd:
        sp=codes[spd].get("spec",{}) or {}
        if sp.get("type")=="enum": cfg.update({"preset_modes":sp.get("range") or [],"preset_mode_state_topic":st,"preset_mode_command_topic":cmd+"/"+spd,"preset_mode_value_template":"{{ value_json.%s }}"%spd})
        else: cfg.update({"percentage_state_topic":st,"percentage_command_topic":cmd+"/"+spd,"percentage_value_template":"{{ value_json.%s }}"%spd})
    return cfg
