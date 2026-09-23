from __future__ import annotations
VACUUM_ROLES={"switch_go":"start","pause":"pause","switch_charge":"return","mode":"mode","status":"status","battery_percentage":"battery","electricity_left":"battery","suction":"fan_speed","seek":"locate"}
def build_vacuum(device_id,name,codes,bt,fan_speeds=None):
    st=bt+"/state"; cmd=bt+"/cmd"
    return {"name":name,"unique_id":"tuya_%s_vacuum"%device_id,"device":{"identifiers":["tuya_%s"%device_id],"name":name,"manufacturer":"Tuya"},
        "availability_topic":bt+"/avail","schema":"state",
        "supported_features":["start","pause","stop","return_home","battery","status","locate","fan_speed","send_command"],
        "command_topic":cmd,"state_topic":st,"send_command_topic":cmd+"/send_command","set_fan_speed_topic":cmd+"/fan_speed",
        "fan_speed_list":fan_speeds or [],"json_attributes_topic":bt+"/attrs"}
