from __future__ import annotations
TUYA_MODE_TO_HVAC={"auto":"auto","cold":"cool","hot":"heat","wet":"dry","wind":"fan_only","cool":"cool","heat":"heat","dry":"dry","fan":"fan_only","ventilation":"fan_only"}
CLIMATE_MAINCODES={"switch","Switch","Power","power","temp_set","Temp_set","temp_set_f","temp_current","Temp_current","mode","Mode","windspeed","fan_speed_enum","up_down_sweep","left_right_sweep","humidity_current","mode_ECO","eco","sleep","mode_dry"}
def _div(sp): return 10**int(sp.get("scale",0) or 0)
def build_climate(device_id,name,codes,bt):
    def has(*c): return any(x in codes for x in c)
    st=bt+"/state"; cmd=bt+"/cmd"
    cfg={"name":name,"unique_id":"tuya_%s_climate"%device_id,"device":{"identifiers":["tuya_%s"%device_id],"name":name,"manufacturer":"Tuya"},
         "availability_topic":bt+"/avail","temperature_state_topic":st,"temperature_command_topic":cmd+"/temp_set",
         "temperature_state_template":"{{ value_json.temp_set }}","temp_step":0.5,
         "mode_state_topic":st,"mode_command_topic":cmd+"/mode","mode_state_template":"{{ value_json.hvac_mode }}"}
    t=codes.get("temp_set") or codes.get("Temp_set")
    if t:
        sp=t.get("spec") or {}; d=_div(sp)
        if sp.get("min") is not None: cfg["min_temp"]=round(sp["min"]/d,1)
        if sp.get("max") is not None: cfg["max_temp"]=round(sp["max"]/d,1)
        cfg["temp_step"]=round((sp.get("step") or 1)/d,2)
    if has("temp_current","Temp_current"):
        cfg["current_temperature_topic"]=st; cfg["current_temperature_template"]="{{ value_json.%s }}"%("temp_current" if has("temp_current") else "Temp_current")
    on_code="Power" if "Power" in codes else ("switch" if "switch" in codes else ("Switch" if "Switch" in codes else "Power"))
    mode_code="mode" if "mode" in codes else ("Mode" if "Mode" in codes else "mode")
    modes=["off"]; md=codes.get("mode") or codes.get("Mode"); mapping={}
    if md:
        for v in ((md.get("spec") or {}).get("range") or []):
            hv=TUYA_MODE_TO_HVAC.get(v, v); mapping[v]=hv
            if hv not in modes: modes.append(hv)
    cfg["modes"]=modes or ["off","auto"]
    # hvac = off si apagado; si no, mapear el modo Tuya -> hvac. Plantilla sobre el dict crudo.
    cfg["mode_state_template"]=("{%% if not value_json.%s %%}off{%% else %%}{{ {%s}.get(value_json.%s|string, value_json.%s) }}{%% endif %%}"
        % (on_code, ", ".join("'%s':'%s'"%(k,v) for k,v in mapping.items()), mode_code, mode_code))
    cfg["power_command_topic"]=cmd+"/power_hvac"
    w=codes.get("windspeed") or codes.get("fan_speed_enum")
    if w: cfg.update({"fan_modes":(w.get("spec") or {}).get("range") or [],"fan_mode_state_topic":st,"fan_mode_command_topic":cmd+"/windspeed","fan_mode_state_template":"{{ value_json.windspeed }}"})
    s=codes.get("up_down_sweep")
    if s: cfg.update({"swing_modes":(s.get("spec") or {}).get("range") or [],"swing_mode_state_topic":st,"swing_mode_command_topic":cmd+"/up_down_sweep","swing_mode_state_template":"{{ value_json.up_down_sweep }}"})
    pr=[c for c in ("mode_ECO","eco","sleep","mode_dry") if has(c)]
    if pr: cfg.update({"preset_modes":pr,"preset_mode_state_topic":st,"preset_mode_command_topic":cmd+"/preset","preset_mode_value_template":"{{ value_json.preset }}"})
    return cfg
