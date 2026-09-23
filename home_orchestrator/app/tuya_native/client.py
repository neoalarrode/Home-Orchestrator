from __future__ import annotations
import hashlib, json, time, urllib.parse, urllib.request, uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional
from . import signing, hmac_signer, encryption
from .encryption import verify_response_sign

API_HOST = "https://a1.tuyaeu.com"; API_PATH = "/api.json"
APP_VERSION = "7.9.0"; TTID = "tuyaSmart"; ET_VERSION = "3"
PKG_NAME = b"com.tuya.smart"


@dataclass
class Credentials:
    app_id: str
    app_secret: str
    cert_der: bytes
    bmp_secret_hex: str
    chkey: str
    device_id: str
    def key(self) -> bytes:
        bmp = self.bmp_secret_hex
        bmp_bytes = bytes.fromhex(bmp) if isinstance(bmp, str) else bmp
        app = self.app_secret.encode() if isinstance(self.app_secret, str) else self.app_secret
        return hmac_signer.build_signing_key(PKG_NAME, self.cert_der, bmp_bytes, app)


class ApiError(Exception):
    def __init__(self, error_code: str, error_msg: str, raw: Mapping[str, Any]):
        super().__init__("%s: %s" % (error_code, error_msg))
        self.error_code = error_code; self.error_msg = error_msg; self.raw = raw


@dataclass
class TuyaMobileClient:
    creds: Credentials
    session_id: Optional[str] = None
    ecode: Optional[str] = None
    opener: Any = field(default=None, repr=False)

    def _get(self, url): 
        if self.opener: return self.opener(url)
        with urllib.request.urlopen(url, timeout=15) as r: return r.read()
    def _post(self, url, body):
        if self.opener: return self.opener(url, body)
        req=urllib.request.Request(url, data=body, method="POST",
            headers={"Content-Type":"application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=15) as r: return r.read()

    def build_params(self, api, v, extra=None):
        p={"a":api,"v":v,"time":str(int(time.time())),"clientId":self.creds.app_id,
           "requestId":uuid.uuid4().hex,"chKey":self.creds.chkey,"et":ET_VERSION,
           "appVersion":APP_VERSION,"ttid":TTID,"os":"Android","deviceId":self.creds.device_id}
        if self.session_id: p["sid"]=self.session_id
        if extra: p.update(extra)
        return p

    def call(self, api, v, post_data=None, *, session_require=False, extra_params=None):
        K=self.creds.key()
        params=self.build_params(api,v,extra_params)
        rid=params["requestId"].encode()
        ecode=self.ecode.encode() if (session_require and self.ecode) else None
        enc_key=encryption.encrypto_key(K, rid, ecode)
        fields=dict(params)
        if post_data is not None:
            fields["postData"]=encryption.encrypt_post_data(enc_key, json.dumps(post_data,separators=(",",":")).encode())
        fields["sign"]=hmac_signer.sign(signing.build_sign_string(fields).encode(), K)
        if post_data is None:
            raw=self._get("%s%s?%s"%(API_HOST,API_PATH,urllib.parse.urlencode(fields)))
        else:
            raw=self._post("%s%s"%(API_HOST,API_PATH), urllib.parse.urlencode(fields).encode())
        resp=json.loads(raw)
        if isinstance(resp,dict) and resp.get("success") is False:
            raise ApiError(resp.get("errorCode","?"),resp.get("errorMsg","?"),resp)
        if isinstance(resp,dict) and "result" in resp and isinstance(resp["result"],str) and "sign" in resp and "t" in resp:
            verify_response_sign(resp["result"],resp["t"],resp["sign"],enc_key.decode())
            inner=json.loads(encryption.decrypt_response(enc_key, resp["result"]))
            if isinstance(inner,dict) and inner.get("success") is False:
                raise ApiError(inner.get("errorCode","?"),inner.get("errorMsg","?"),inner)
            return inner.get("result",inner) if isinstance(inner,dict) else inner
        return resp.get("result",resp) if isinstance(resp,dict) else resp

    def publish_dps(self, dev_id, dps, gw_id=None):
        return self.call("thing.m.device.dp.publish","1.0",
            post_data={"gwId":gw_id or dev_id,"devId":dev_id,"dps":json.dumps(dict(dps),separators=(",",":"))},
            session_require=True)
