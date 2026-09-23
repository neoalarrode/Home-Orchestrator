from __future__ import annotations
def build_cover(device_id,name,codes,bt):
    st=bt+"/state"; cmd=bt+"/cmd"
    cfg={"name":None,"unique_id":"tuya_%s_cover"%device_id,"device":{"identifiers":["tuya_%s"%device_id],"name":name,"manufacturer":"Tuya"},"availability_topic":bt+"/avail","command_topic":cmd+"/control","payload_open":"open","payload_close":"close","payload_stop":"stop","state_topic":st,"value_template":"{{ value_json.control_state | default('') }}"}
    if "percent_control" in codes or "position" in codes:
        pc="percent_control" if "percent_control" in codes else "position"
        cfg.update({"position_topic":st,"position_template":"{{ value_json.%s }}"%pc,"set_position_topic":cmd+"/position","position_open":100,"position_closed":0})
    return cfg
