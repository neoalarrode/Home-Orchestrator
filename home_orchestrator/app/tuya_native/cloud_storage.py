"""Descarga de ficheros del almacenamiento en la nube del modulo sweeper
(mapas/rutas/historial): AWS SigV4 presigned-URL ESTANDAR, sin necesidad de la
libreria nativa libThingCloudStorageSignatureTools.so.

VERIFICADO en vivo contra el servidor real de Tuya: con las credenciales
temporales de `thing.m.dev.storage.config.get` (ak/sk/token/bucket/endpoint/
region, provider="s3"), presign_get() genera una URL que el servidor ACEPTA
(200 descargando un fichero de mapa real). Esquema confirmado cruzando el codigo
Java (com/thingclips/sdk/sweeper/bppdpdq.java) con las cadenas del .so (mismos
formatos: host=f"{bucket}.{endpoint}", credential scope "%s/%s/s3/aws4_request",
X-Amz-SignedHeaders=host, UNSIGNED-PAYLOAD).
"""
from __future__ import annotations
import datetime, hashlib, hmac, urllib.parse, urllib.request
from dataclasses import dataclass


@dataclass
class CloudStorageConfig:
    """Credenciales TEMPORALES de thing.m.dev.storage.config.get (expiran; nunca
    guardar en el repo)."""
    ak: str; sk: str; token: str; bucket: str; endpoint: str; region: str; path_common: str

    @classmethod
    def from_json(cls, d: dict) -> "CloudStorageConfig":
        return cls(ak=d["ak"], sk=d["sk"], token=d["token"], bucket=d["bucket"],
                   endpoint=d["endpoint"], region=d.get("region") or "us-east-1",
                   path_common=(d.get("pathConfig") or {}).get("common") or "")

    @property
    def layout_path(self) -> str: return self.path_common + "/layout/lay.bin"
    @property
    def route_path(self) -> str: return self.path_common + "/route/rou.bin"


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(sk: str, datestamp: str, region: str, service: str) -> bytes:
    return _sign(_sign(_sign(_sign(("AWS4" + sk).encode("utf-8"), datestamp), region), service), "aws4_request")


def presign_get(cfg: CloudStorageConfig, path: str, expires: int = 600) -> str:
    """URL GET presignada AWS SigV4 (query-string, service='s3'). El gateway de
    Tuya acepta cualquier `region` (se usa el campo de la API o 'us-east-1')."""
    region = cfg.region or "us-east-1"
    host = "%s.%s" % (cfg.bucket, cfg.endpoint)
    t = datetime.datetime.utcnow()
    amz = t.strftime("%Y%m%dT%H%M%SZ"); ds = t.strftime("%Y%m%d")
    uri = urllib.parse.quote(path)
    q = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256",
         "X-Amz-Credential": "%s/%s/%s/s3/aws4_request" % (cfg.ak, ds, region),
         "X-Amz-Date": amz, "X-Amz-Expires": str(expires),
         "X-Amz-Security-Token": cfg.token, "X-Amz-SignedHeaders": "host"}
    cq = "&".join("%s=%s" % (urllib.parse.quote(k, safe=""), urllib.parse.quote(q[k], safe="")) for k in sorted(q))
    creq = "GET\n%s\n%s\nhost:%s\n\nhost\nUNSIGNED-PAYLOAD" % (uri, cq, host)
    scope = "%s/%s/s3/aws4_request" % (ds, region)
    sts = "AWS4-HMAC-SHA256\n%s\n%s\n%s" % (amz, scope, hashlib.sha256(creq.encode("utf-8")).hexdigest())
    sig = hmac.new(_signing_key(cfg.sk, ds, region, "s3"), sts.encode("utf-8"), hashlib.sha256).hexdigest()
    return "https://%s%s?%s&X-Amz-Signature=%s" % (host, uri, cq, sig)


def fetch(cfg: CloudStorageConfig, path: str, timeout: int = 25) -> bytes:
    """GET de solo lectura de un objeto del bucket (puede lanzar urllib.error)."""
    with urllib.request.urlopen(presign_get(cfg, path), timeout=timeout) as r:
        return r.read()
