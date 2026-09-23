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
        self.ctl.load_profile()
        from . import profiles
        tm=self.api.call("thing.m.product.thing.model","1.0",post_data={"productId":self.product_id,"productVersion":"1.0.0"},session_require=True)
        self.codes=profiles.parse_thing_model(tm)
        try: live=self.ctl.get_state()
        except Exception: live=None
        self.plan=ent_gen.build_entities(self.category,self.codes,self.name,self.device_id,state=live,expose_advanced=self.expose_advanced)
        return self
    def state_payload(self): return self.ctl.get_state()
    def handle_command(self,domain,command,payload):
        if domain=="vacuum": return self._vac(command,payload)
        if domain=="light": return self._light(payload)
        return self.ctl.set_dp(command,payload)
    def _vac(self,command,payload):
        if command=="start": return self.ctl.set_dp("switch_go",True,prefer="mqtt")
        if command=="pause": return self.ctl.set_dp("pause",True,prefer="mqtt")
        if command=="return_to_base": return self.ctl.set_dp("switch_charge",True,prefer="mqtt")
        if command=="locate": return self.ctl.set_dp("seek",True,prefer="mqtt")
        if command=="set_fan_speed": return self.ctl.set_dp("suction",payload,prefer="mqtt")
        if command=="send_command":
            p=payload if isinstance(payload,dict) else json.loads(payload)
            if p.get("command")=="clean_rooms":
                self.ctl.publish_message(mqtt_transport.room_clean_message(p["params"]["ids"],clean_times=1),protocol=64)
                self.ctl.set_dps({"mode":"select_room"},prefer="mqtt")
                return self.ctl.set_dp("switch_go",True,prefer="mqtt")
        return False
    def _light(self,payload):
        ok=True
        if "state" in payload: ok&=self.ctl.set_dp("switch_led",payload["state"]=="ON")
        if "brightness" in payload: ok&=self.ctl.set_dp("bright_value",lightkit.brightness_from_ha(int(payload["brightness"])))
        if "color" in payload and "h" in payload["color"]:
            hs=payload["color"]; ok&=self.ctl.set_dp("colour_data",lightkit.encode_colour(int(hs["h"]),int(hs["s"]),100))
        return ok
    def discovery_configs(self):
        bt="tuya_native/%s"%self.device_id; out=[]
        for e in self.plan.get("entities",[]):
            dom=e["domain"]; oid="%s_%s"%(self.device_id,e.get("code") or e.get("role") or dom)
            out.append({"topic":"%s/%s/tuya/%s/config"%(HA_DISCOVERY_PREFIX,dom,oid),"payload":e.get("config") or ha_aux.build_aux(e,self.device_id,self.name,bt)})
        return out
