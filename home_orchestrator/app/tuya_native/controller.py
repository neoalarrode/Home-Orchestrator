"""Controlador generico: perfil dinamico (code<->dpId) + leer/escribir por code;
transporte HTTP publishDps por defecto, MQTT (localKey) para lo que lo ignore."""
from __future__ import annotations
import time
from typing import Any, Mapping, Optional
from . import profiles
class DeviceController:
    def __init__(self, api, device_id, *, local_key=None, mqtt_transport=None,
                 mqtt_session=None, product_id="", category=""):
        self.api=api; self.device_id=device_id; self.local_key=local_key
        self.mqtt=mqtt_transport; self.mqtt_session=mqtt_session
        self.product_id=product_id; self.category=category
        self._c2d={}; self._d2c={}
    def load_profile(self):
        tm=self.api.call("thing.m.product.thing.model","1.0",
            post_data={"productId":self.product_id,"productVersion":"1.0.0"},session_require=True)
        for code,info in profiles.parse_thing_model(tm).items():
            self._c2d[code]=int(info["dp"]); self._d2c[str(info["dp"])]=code
        return self
    def dp_id(self,code): return self._c2d[code]
    def get_state(self):
        st=self.api.call("thing.m.device.dp.get","1.0",post_data={"devId":self.device_id},session_require=True)
        return {self._d2c.get(k,k):v for k,v in st.items()} if isinstance(st,dict) else {}
    def set_dp(self,code,value,*,prefer="http"):
        dps={str(self.dp_id(code)):value}
        if prefer=="http":
            self.api.publish_dps(self.device_id,dps)
            if str(self.get_state().get(code))==str(value): return True
        return self._mqtt(dps) and str(self.get_state().get(code))==str(value)
    def set_dps(self,mapping,*,prefer="http"):
        dps={str(self.dp_id(c)):v for c,v in mapping.items()}
        if prefer=="http": self.api.publish_dps(self.device_id,dps); return True
        return self._mqtt(dps)
    def publish_message(self,message,*,protocol):
        if not (self.mqtt and self.local_key and self.mqtt_session): return False
        import paho.mqtt.client as mqtt, ssl
        ms=self.mqtt_session; cr=self.mqtt.derive_credentials(ms["sid"],ms["ecode"],ms["uid"],ms.get("device_id"))
        cl=mqtt.Client(client_id=cr.client_id,protocol=mqtt.MQTTv311); cl.username_pw_set(cr.username,cr.password)
        cl.tls_set(cert_reqs=ssl.CERT_NONE); cl.tls_insecure_set(True); cl.connect(cr.host,cr.port,60); cl.loop_start(); time.sleep(2)
        fr,_,_=self.mqtt.build_frame(message,self.local_key,protocol=protocol); cl.publish(self.mqtt.TOPIC_PUB+self.device_id,fr); time.sleep(2)
        cl.loop_stop(); cl.disconnect(); return True
    def _mqtt(self,dps,protocol=5): return self.publish_message({"dps":dict(dps)},protocol=protocol)
