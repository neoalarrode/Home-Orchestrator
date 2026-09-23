from __future__ import annotations
from typing import Mapping
import hashlib

# whitelist de 20 claves para la cadena de firma (orden alfabetico, unidas por "||")
PAIR_SEP = "||"; KV_SEP = "="
SIGN_KEYS = {"a","v","time","clientId","requestId","chKey","et","appVersion","ttid","os",
             "deviceId","sid","postData","lang","osSystem","sdkVersion","bizData","nd","platform","channel"}

def swap_sign_string(h: str) -> str:
    """reordena bloques de 8: [8:16][0:8][24:32][16:24]... (swapSignString del APK)."""
    out=[]
    for i in range(0, len(h), 16):
        a=h[i:i+8]; b=h[i+8:i+16]
        out.append(b+a)
    return "".join(out)

def post_data_digest(encrypted_post_data: str) -> str:
    return swap_sign_string(hashlib.md5(encrypted_post_data.encode()).hexdigest())

def build_sign_string(fields: Mapping[str, str]) -> str:
    parts=[]
    for k in sorted(fields):
        if k not in SIGN_KEYS: continue
        v=fields[k]
        if v is None or v=="": continue
        if k=="postData":
            v=post_data_digest(v)
        parts.append("%s%s%s"%(k,KV_SEP,v))
    return PAIR_SEP.join(parts)
