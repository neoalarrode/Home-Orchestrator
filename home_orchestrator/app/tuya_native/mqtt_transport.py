"""Canal MQTT de la app Tuya (app<->device). VERIFICADO en vivo (el robot arranco).
- broker ssl://m1.tuyaeu.com:8883; username=sid; password=md5hex(ecode)[8:24];
  clientId=com.tuya.smart_mb_<deviceId>_<md5hex(uid+"sdkfasodifca")>_DEFAULT.
- PUBLICAR en smart/mb/out/<devId>; SUSCRIBIR smart/mb/in/<devId>.
- frame pv2.3: header(12)=aad = "2.3"+seq(4B BE)+b"\\x00\\x00\\x00\\x01"+b"\\x00";
  nonce(12) a continuacion; AES-GCM(key=localKey) del JSON {"protocol":P,"t":epoch,"data":msg}.
"""
from __future__ import annotations
import hashlib, json, os, ssl, struct, time
from dataclasses import dataclass
from typing import Any, Mapping, Optional

MQTT_HOST_EU = "m1.tuyaeu.com"; MQTT_PORT = 8883
TOPIC_PUB = "smart/mb/out/"   # app -> device (VERIFICADO)
TOPIC_SUB = "smart/mb/in/"    # device -> app
SALT_CLIENTID = "sdkfasodifca"
PROTOCOL_APP_TO_ROBOT = 64

def _md5hex(s: str) -> str: return hashlib.md5(s.encode()).hexdigest()

@dataclass
class MqttCredentials:
    username: str; password: str; client_id: str
    host: str = MQTT_HOST_EU; port: int = MQTT_PORT

def derive_credentials(sid, ecode, uid, device_id, host=MQTT_HOST_EU) -> MqttCredentials:
    return MqttCredentials(username=sid, password=_md5hex(ecode)[8:24],
        client_id="com.tuya.smart_mb_%s_%s_DEFAULT" % (device_id, _md5hex(uid + SALT_CLIENTID)), host=host)

def build_frame(message, local_key: bytes, *, protocol=PROTOCOL_APP_TO_ROBOT, pv="2.3",
                seq=None, t=None, nonce=None):
    from Crypto.Cipher import AES
    if len(local_key) != 16: raise ValueError("localKey debe ser 16 bytes")
    if seq is None: import random; seq = random.randint(1, 0xFFFFFF)
    if t is None: t = int(time.time())
    header = pv.encode() + struct.pack(">I", seq & 0xFFFFFFFF) + b"\x00\x00\x00\x01" + b"\x00"
    plaintext = json.dumps({"protocol": protocol, "t": t, "data": dict(message)}, separators=(",", ":")).encode()
    nonce = nonce or os.urandom(12)
    c = AES.new(local_key, AES.MODE_GCM, nonce=nonce); c.update(header)
    ct, tag = c.encrypt_and_digest(plaintext)
    return header + nonce + ct + tag, header, plaintext

def decrypt_frame(frame: bytes, local_key: bytes, *, pv="2.3") -> Optional[bytes]:
    from Crypto.Cipher import AES
    hlen = len(pv.encode()) + 9
    if len(frame) < hlen + 12 + 16: return None
    aad, nonce, ct, tag = frame[:hlen], frame[hlen:hlen+12], frame[hlen+12:-16], frame[-16:]
    try:
        c = AES.new(local_key, AES.MODE_GCM, nonce=nonce); c.update(aad)
        return c.decrypt_and_verify(ct, tag)
    except Exception:
        return None

def room_clean_message(room_ids, *, clean_times=2, suction="normal", cistern="high",
                       sweep_mop_mode="both_work", y_mop=0, version="1.0.0", task_id=None) -> dict:
    n = len(room_ids)
    return {"reqType":"roomCleanSet","version":version,"taskId":task_id or str(int(time.time()*1000)),
            "ids":list(room_ids),"suctions":[suction]*n,"cisterns":[cistern]*n,"cleanCounts":[clean_times]*n,
            "yMops":[y_mop]*n,"sweepMopModes":[sweep_mop_mode]*n,"num":n}
