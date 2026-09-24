"""F1 de "Todo lo gordo": descarga y cache del PANEL (mini-programa Godzilla/Ray)
de cada producto. El panel trae la logica/encoders REALES de la app; el motor JS
(F2) lo ejecutara. Aqui solo: resolver miniprogramId, bajar el paquete y extraerlo.

Cadena (VERIFICADA a mano):
  1. thing.m.product.ui.info.batch.get (pids=[productId]) -> uiInfo.bizClientId = miniprogramId.
  2. thing.m.miniprogram.info.get v4.0 (miniprogramId, jssdkVersion, bundleId, paramsJson)
     -> codeDownloadUrl (CDN, tar.gz EN CLARO) + miniprogramVersion.
  3. Descargar tar.gz y extraer (app-service.json / app-config.json / main.js / chunks).
Cache por productId+version en <cache_dir>/<productId>/<version>/.
"""
from __future__ import annotations
import io, json, os, re, tarfile, urllib.request

JSSDK_VERSION = "3.0.0"           # GZLMiniAppVersion.getJssdkVersion() default
BUNDLE_ID = "com.tuya.smart"      # GZLMiniAppUtil.i().getPackageName() (app Tuya Smart)

# El server valida que el CONTENEDOR soporte las versiones de kit que el panel
# necesita (app-config.json.dependencies). Con dependencias vacias responde
# BUSI_PROGRAM_VERSION_NOT_SUPPORT. Declaramos versiones muy altas de todos los
# kits conocidos -> satisface a cualquier panel. VERIFICADO en vivo.
_KITS = ["BaseKit", "MiniKit", "DeviceKit", "BizKit", "P2PKit", "IPCKit", "SweeperKit",
         "MapKit", "LightKit", "MediaKit", "MediaPlayerKit", "CategoryCommonBizKit",
         "HealthKit", "WearKit", "AVideoKit", "PlayNetKit", "OutdoorKit", "IPCAiKit"]
DEPENDENCIES = {k: "99.0.0" for k in _KITS}


def _biz_client_id(client, product_id: str) -> str | None:
    ui = client.call("thing.m.product.ui.info.batch.get", "1.0",
                     post_data={"pids": json.dumps([product_id])}, session_require=True)
    s = json.dumps(ui)
    m = re.search(r'"bizClientId"\s*:\s*"([^"]+)"', s)
    return m.group(1) if m else None


def _code_download_url(client, miniprogram_id: str, product_id: str) -> tuple[str | None, str | None]:
    params_json = json.dumps({"dependencies": DEPENDENCIES, "userMark": "", "channelId": "", "productId": product_id})
    r = client.call("thing.m.miniprogram.info.get", "4.0",
                    post_data={"miniprogramId": miniprogram_id, "jssdkVersion": JSSDK_VERSION,
                               "bundleId": BUNDLE_ID, "paramsJson": params_json},
                    session_require=True)
    s = json.dumps(r)
    url = re.search(r'"codeDownloadUrl"\s*:\s*"([^"]+)"', s)
    url = url.group(1) if url else None
    ver = re.search(r'"(?:miniprogramVersion|version)"\s*:\s*"([^"]+)"', s)
    ver = ver.group(1) if ver else None
    # fallback: la version va embebida en la URL (...-miniprogram-<ver>-<hash>-...)
    if not ver and url:
        mu = re.search(r'-miniprogram-([0-9][0-9.]*)-[0-9a-f]{8,}', url)
        if mu:
            ver = mu.group(1)
    return (url, ver)


def _extract(tgz: bytes, dest: str) -> None:
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as tf:
        for m in tf.getmembers():
            # aplanar: los ficheros del panel van en la raiz del dest (sin rutas absolutas/..)
            name = os.path.basename(m.name)
            if not name or not m.isfile():
                continue
            f = tf.extractfile(m)
            if f is None:
                continue
            with open(os.path.join(dest, name), "wb") as out:
                out.write(f.read())


def fetch_panel(client, product_id: str, cache_dir: str = "/data/tuya_panels") -> dict | None:
    """Devuelve {product_id, version, dir, miniprogram_id} con el panel extraido
    (cacheado). None si no se pudo. NO ejecuta nada: solo descarga/extrae."""
    mid = _biz_client_id(client, product_id)
    if not mid:
        return None
    url, ver = _code_download_url(client, mid, product_id)
    if not url:
        return None
    ver = ver or "unknown"
    dest = os.path.join(cache_dir, product_id, ver)
    if not (os.path.isdir(dest) and os.path.exists(os.path.join(dest, "app-service.json"))):
        with urllib.request.urlopen(url, timeout=60) as r:
            tgz = r.read()
        _extract(tgz, dest)
    return {"product_id": product_id, "version": ver, "dir": dest, "miniprogram_id": mid}


def load_manifest(panel_dir: str) -> dict:
    """app-service.json (scripts a cargar en el motor) + app-config.json (paginas)."""
    out = {}
    for name in ("app-service.json", "app-config.json"):
        p = os.path.join(panel_dir, name)
        if os.path.exists(p):
            try:
                out[name] = json.load(open(p, encoding="utf-8"))
            except Exception:
                out[name] = None
    return out
