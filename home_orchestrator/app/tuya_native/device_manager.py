"""Runtime del plugin: por dispositivo, perfil dinamico -> entidades HA nativas,
discovery+estado por MQTT, y comandos HA -> DPs / encoders de kit."""
from __future__ import annotations
import json
from typing import Any, Dict, Mapping
from . import entities as ent_gen, controller as ctrl, mqtt_transport, ha_aux
from .kits import light as lightkit
HA_DISCOVERY_PREFIX="homeassistant"
class TuyaDevice:
    def __init__(self,api,device_id,category,product_id,name,*,local_key=None,mqtt_transport=None,mqtt_session=None,expose_advanced=False):
        self.api=api; self.device_id=device_id; self.category=category; self.product_id=product_id; self.name=name
        self.ctl=ctrl.DeviceController(api,device_id,local_key=local_key,mqtt_transport=mqtt_transport,mqtt_session=mqtt_session,product_id=product_id,category=category)
        self.expose_advanced=expose_advanced; self.codes={}; self.plan={}
    def refresh_profile(self):
        self.codes=self.ctl.load_profile()   # una sola descarga del thing-model
        try: live=self.ctl.get_state()
        except Exception: live=None
        self.plan=ent_gen.build_entities(self.category,self.codes,self.name,self.device_id,state=live,expose_advanced=self.expose_advanced)
        return self
    def state_payload(self): return self.ctl.get_state()

    def _light_state(self, raw):
        on=bool(raw.get("switch_led") or raw.get("switch_led_1"))
        o={"state":"ON" if on else "OFF"}
        bv=raw.get("bright_value_v2") or raw.get("bright_value")
        if bv is not None:
            try: o["brightness"]=lightkit.brightness_to_ha(int(bv))
            except Exception: pass
        cd=raw.get("colour_data_v2") or raw.get("colour_data")
        tv=raw.get("temp_value_v2") if raw.get("temp_value_v2") is not None else raw.get("temp_value")
        if cd:
            h,sv,_=lightkit.decode_colour(cd); o["color_mode"]="hs"; o["color"]={"h":h,"s":sv}
        elif tv is not None:
            o["color_mode"]="color_temp"
            # DP interno 0..1000 -> Kelvin (rango fijo 2700..6500) -> mireds, para
            # que el slider de temperatura de color refleje la posicion en HA.
            try:
                frac=max(0,min(1000,int(tv)))/1000; k=2700+frac*3800
                o["color_temp"]=round(1_000_000/k)
            except Exception: pass
        return o

    def _vacuum_state(self, raw):
        from .ha_vacuum import build_vacuum  # noqa
        from .kits import light  # noqa
        STATUS={"standby":"idle","charging":"docked","chargecompleted":"docked","charge_done":"docked",
                "sleep":"idle","paused":"paused","goto_charge":"returning","smart":"cleaning","select_room":"cleaning",
                "zone_clean":"cleaning","cleaning":"cleaning","washing":"cleaning","airing":"docked","collecting_dust":"docked"}
        st=raw.get("status"); o={"state":STATUS.get(st,"cleaning" if raw.get("switch_go") else "idle")}
        bat=raw.get("battery_percentage") or raw.get("electricity_left")
        if bat is not None: o["battery_level"]=int(bat)
        if raw.get("suction") is not None: o["fan_speed"]=raw.get("suction")
        return o

    def state_messages(self):
        """Topic -> payload para publicar el estado. Crudo para las entidades de
        plantilla; luz/aspirador con su forma HA."""
        raw=self.state_payload(); bt="tuya_native/%s"%self.device_id
        msgs={bt+"/state": raw}
        dom=self.plan.get("main_domain")
        if dom=="light": msgs[bt+"/light"]=self._light_state(raw)
        elif dom=="vacuum": msgs[bt+"/vacuum"]=self._vacuum_state(raw)
        return msgs
    def handle_command(self,domain,command,payload):
        if domain=="vacuum": return self._vac(command,payload)
        if domain=="light": return self._light(payload)
        if domain=="climate": return self._climate(command,payload)
        return self.ctl.set_dp(command,payload)
    def _real_code(self,name):
        """Nombre real del codigo (case-insensitive) o None -- el set estandar
        varia el casing por dispositivo (Switch vs switch)."""
        low={k.lower():k for k in self.codes}
        return low.get(str(name).lower())
    def _climate(self,command,payload):
        """Comandos climate de HA (MQTT) traducidos via el MISMO handle que usa
        el consumo interno (modo HA->enum Tuya, escala de temperatura, on/off por
        el switch real) -- antes iban crudos a set_dp y no controlaban el aparato."""
        from .handles import TuyaClimateHandle
        h=TuyaClimateHandle(self.ctl,self.codes)
        if command=="temp_set":
            h.set_temperature(float(payload)); return True
        if command=="mode":
            h.set_hvac_mode(str(payload)); return True   # off->switch off, cool->cold, ...
        if command=="windspeed":
            h.set_fan_mode(str(payload)); return True
        if command=="power_hvac":
            on=str(payload).strip().upper() in ("ON","1","TRUE")
            if h._sw: self.ctl.set_dp(h._sw,on)
            return True
        if command=="up_down_sweep":
            code=self._real_code("up_down_sweep")
            if code: self.ctl.set_dp(code,payload); return True
            return False
        if command=="preset":
            code=self._real_code(str(payload))
            if code and (self.codes.get(code) or {}).get("type")=="bool":
                self.ctl.set_dp(code,True); return True
            return False
        # cualquier otro code: set directo si existe
        code=self._real_code(command)
        return self.ctl.set_dp(code,payload) if code else False
    def _vac(self,command,payload):
        if command=="start": return self.ctl.set_dp("switch_go",True,prefer="mqtt")
        # HA MQTT vacuum manda payload "stop" y "pause" por el command_topic; ambos
        # se tratan como pausa (parar el ciclo) -- no hay DP de "stop" separado.
        if command in ("pause","stop"): return self.ctl.set_dp("pause",True,prefer="mqtt")
        if command=="return_to_base": return self.ctl.set_dp("switch_charge",True,prefer="mqtt")
        if command=="locate": return self.ctl.set_dp("seek",True,prefer="mqtt")
        # El sub-topic real es "fan_speed" (set_fan_speed_topic=cmd+"/fan_speed");
        # se acepta tambien "set_fan_speed" por compatibilidad.
        if command in ("fan_speed","set_fan_speed"): return self.ctl.set_dp("suction",payload,prefer="mqtt")
        if command=="send_command":
            p=payload if isinstance(payload,dict) else json.loads(payload)
            if p.get("command")=="clean_rooms":
                self.ctl.publish_message(mqtt_transport.room_clean_message(p["params"]["ids"],clean_times=1),protocol=64)
                self.ctl.set_dps({"mode":"select_room"},prefer="mqtt")
                return self.ctl.set_dp("switch_go",True,prefer="mqtt")
            return False
        # Companeros curados (sweep_mop_mode, water_output, clean_times, ...): el
        # sub-topic ES el code -> set directo del DP por MQTT (local_key).
        code=self._real_code(command)
        if code: return self.ctl.set_dp(code,payload,prefer="mqtt")
        return False
    def _light(self,payload):
        """Comando de luz de HA (schema json) via TuyaLightHandle -- usa _first()
        para casar switch_led/bright_value/colour_data y sus variantes v2/_1 (antes
        codigos fijos -> KeyError en bombillas v2) y ademas soporta color_temp."""
        from .handles import TuyaLightHandle
        if not isinstance(payload,dict): return False
        h=TuyaLightHandle(self.ctl,self.codes)
        if str(payload.get("state","")).upper()=="OFF":
            h.turn_off(); return True
        kw={}
        if "brightness" in payload:
            try: kw["brightness_pct"]=round(int(payload["brightness"])*100/255,1)
            except Exception: pass
        if "color_temp" in payload:
            try:
                mireds=float(payload["color_temp"])
                if mireds>0: kw["color_temp_kelvin"]=round(1_000_000/mireds)
            except Exception: pass
        col=payload.get("color")
        if isinstance(col,dict) and "h" in col and "s" in col:
            try: kw["hs"]=(float(col["h"]),float(col["s"]))
            except Exception: pass
        h.turn_on(**kw); return True
    def discovery_configs(self):
        bt="tuya_native/%s"%self.device_id; out=[]
        for e in self.plan.get("entities",[]):
            dom=e["domain"]; oid="%s_%s"%(self.device_id,e.get("code") or e.get("role") or dom)
            out.append({"topic":"%s/%s/tuya/%s/config"%(HA_DISCOVERY_PREFIX,dom,oid),"payload":e.get("config") or ha_aux.build_aux(e,self.device_id,self.name,bt)})
        return out
