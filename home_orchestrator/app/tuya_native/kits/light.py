from __future__ import annotations
def decode_colour(h):
    h=h or ""
    if len(h)>=12: return int(h[0:4],16),round(int(h[4:8],16)/10),round(int(h[8:12],16)/10)
    if len(h)>=10: return int(h[0:4],16),round(int(h[4:6],16)/2.55),round(int(h[6:8],16)/2.55)
    return 0,0,0
def encode_colour(h,s,v,*,v2=True):
    h%=360
    return "%04x%04x%04x"%(h,int(round(s*10)),int(round(v*10))) if v2 else "%04x%02x%02x"%(h,int(round(s*2.55)),int(round(v*2.55)))
def brightness_to_ha(raw,dp_max=1000,dp_min=10): raw=max(dp_min,min(dp_max,raw)); return round((raw-dp_min)*255/(dp_max-dp_min))
def brightness_from_ha(ha,dp_max=1000,dp_min=10): ha=max(0,min(255,ha)); return round(dp_min+ha*(dp_max-dp_min)/255)
LIGHT_ROLE={"switch_led":"state","switch_led_1":"state","bright_value":"brightness","bright_value_v2":"brightness","temp_value":"color_temp","temp_value_v2":"color_temp","colour_data":"hs","colour_data_v2":"hs","work_mode":"effect","scene_data":"scene","scene_data_v2":"scene"}
