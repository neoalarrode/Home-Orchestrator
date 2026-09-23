"""Luz NATIVA de HA (MQTT Discovery schema json). El estado se publica traducido
al formato HA ({state,brightness,color_mode,color,color_temp}) en un topic propio.
NOTA: 'effect' se omite por ahora (HA rechazaba el discovery; pendiente de validar
el formato exacto de effect en una prueba controlada)."""
from __future__ import annotations
def build_light(device_id,name,codes,bt):
    def has(*c): return any(x in codes for x in c)
    st=bt+"/light"; cmd=bt+"/set"     # topic de estado PROPIO (traducido)
    cfg={"schema":"json","name":name,"unique_id":"tuya_%s_light"%device_id,
         "device":{"identifiers":["tuya_%s"%device_id],"name":name,"manufacturer":"Tuya"},
         "availability_topic":bt+"/avail","state_topic":st,"command_topic":cmd}
    modes=[]
    if has("colour_data","colour_data_v2"): modes.append("hs")
    if has("temp_value","temp_value_v2"): modes.append("color_temp")
    if not modes: modes=["brightness"] if has("bright_value","bright_value_v2") else ["onoff"]
    cfg["supported_color_modes"]=modes
    if modes!=["onoff"]: cfg["brightness"]=True; cfg["brightness_scale"]=255
    return cfg
