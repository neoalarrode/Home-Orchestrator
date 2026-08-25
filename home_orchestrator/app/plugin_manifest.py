"""
Registro REMOTO de versiones de plugin -- lo que rompe el acoplamiento entre
publicar un plugin y publicar el core.

EL PROBLEMA QUE RESUELVE
------------------------
Core y plugins son dos capas separadas a proposito: el core viaja en la
imagen del addon, y cada plugin se descarga aparte, verificado por sha256
(ver plugin_downloader.py). Pero hasta ahora la lista de "que version es la
buena de cada plugin" (`PLUGIN_CATALOG`, con su tag y su sha256) vivia DENTRO
de la imagen del core. Y eso ata las dos capas por la puerta de atras:
publicar un arreglo de un plugin obligaba a publicar tambien el core, solo
para re-pinear el catalogo. Dos releases por cada cambio.

Peor que la molestia: la segunda se olvida. Paso de verdad -- el plugin de
Tuya estuvo SIETE versiones sin re-pinear, con cada arreglo publicado y
ninguno activo, y nadie tenia forma de notarlo: la version del addon subia,
el CHANGELOG prometia los arreglos, y corria el codigo de varias versiones
atras.

COMO
----
La fuente de verdad se saca de la imagen y se pone en `plugins.json`, en la
rama principal del repo, que se lee EN CALIENTE. Publicar un plugin pasa a
ser: subir su codigo con su tag y actualizar ese fichero. Ningun addon
necesita actualizarse para enterarse.

CONFIANZA
---------
No se relaja nada. El manifiesto llega por HTTPS desde nuestro propio repo, y
lo que de verdad protege el codigo -- el sha256 de cada tarball, verificado
antes de tocar disco -- sigue igual: solo cambia DONDE se lee ese sha256. Un
manifiesto que no se pueda leer, o que venga con una forma que no cuadre, se
descarta entero y se sigue con el catalogo de la imagen: mejor la version
anterior funcionando que una instalacion a medias.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request

log = logging.getLogger("plugin_manifest")

REPO = "neoalarrode/Home-Orchestrator"
RAMA = os.environ.get("PLUGIN_MANIFEST_BRANCH", "main")
URL = os.environ.get(
    "PLUGIN_MANIFEST_URL",
    f"https://raw.githubusercontent.com/{REPO}/{RAMA}/plugins.json",
)
CACHE_PATH = os.environ.get("PLUGIN_MANIFEST_CACHE", "/data/plugins/manifest.json")
FETCH_TIMEOUT_SECONDS = 20
# Cuanto vale una lectura antes de volver a preguntar. No hace falta apretar:
# un plugin no se publica cada minuto, y el arranque siempre fuerza una.
TTL_SECONDS = 6 * 3600

# Campos sin los cuales una entrada no sirve para descargar nada. Una entrada
# incompleta se ignora (se cae a la de la imagen) en vez de romper la carga.
REQUERIDOS = ("tag", "sha256", "files")

_lock = threading.Lock()
_cache: dict | None = None
_cache_ts: float = 0.0


def _validar(entrada: dict) -> bool:
    if not all(entrada.get(k) for k in REQUERIDOS):
        return False
    if not isinstance(entrada.get("files"), list) or not entrada["files"]:
        return False
    sha = entrada["sha256"]
    # Un sha256 son 64 caracteres hex. Comprobarlo aqui evita que una errata
    # en el manifiesto se convierta en un fallo raro dentro del descargador.
    return isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha.lower())


def _parsear(crudo: bytes) -> dict[str, dict]:
    datos = json.loads(crudo.decode("utf-8"))
    plugins = datos.get("plugins")
    if not isinstance(plugins, list):
        raise ValueError("el manifiesto no trae una lista 'plugins'")
    fuera: dict[str, dict] = {}
    for entrada in plugins:
        slug = entrada.get("slug")
        if not slug:
            continue
        if not _validar(entrada):
            # Normal durante la transicion: una entrada que todavia no declara
            # tag/sha256 simplemente no manda, y se usa la de la imagen.
            log.debug("Manifiesto: '%s' no declara tag/sha256 utilizables -- se ignora", slug)
            continue
        fuera[slug] = entrada
    return fuera


def _leer_cache() -> dict[str, dict] | None:
    try:
        with open(CACHE_PATH, "rb") as f:
            return _parsear(f.read())
    except FileNotFoundError:
        return None
    except Exception:
        log.debug("Manifiesto: cache ilegible en %s", CACHE_PATH, exc_info=True)
        return None


def _guardar_cache(crudo: bytes) -> None:
    """Guardar la ULTIMA lectura buena es lo que hace que un arranque sin red
    siga sabiendo cual era la version buena, en vez de retroceder a la de la
    imagen."""
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "wb") as f:
            f.write(crudo)
        os.replace(tmp, CACHE_PATH)
    except Exception:
        log.debug("Manifiesto: no se ha podido guardar la cache", exc_info=True)


def fetch(force: bool = False) -> dict[str, dict]:
    """Entradas del manifiesto remoto, por slug. Diccionario vacio si no hay
    ninguna utilizable -- quien llama se queda entonces con el catalogo de la
    imagen. NUNCA lanza."""
    global _cache, _cache_ts
    with _lock:
        if not force and _cache is not None and (time.time() - _cache_ts) < TTL_SECONDS:
            return _cache

        entradas: dict[str, dict] = {}
        try:
            req = urllib.request.Request(URL, headers={"User-Agent": "home-orchestrator"})
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as r:
                crudo = r.read()
            entradas = _parsear(crudo)
            _guardar_cache(crudo)
            log.info("Manifiesto de plugins leido: %d entrada(s) utilizables", len(entradas))
        except Exception:
            log.warning(
                "No se ha podido leer el manifiesto remoto de plugins (%s) -- se usa "
                "la ultima lectura buena, o el catalogo de la imagen", URL, exc_info=True,
            )
            entradas = _leer_cache() or {}

        _cache, _cache_ts = entradas, time.time()
        return entradas


def catalogo_efectivo(base: dict[str, dict], force: bool = False) -> dict[str, dict]:
    """`base` (el catalogo de la imagen) con el manifiesto remoto por encima.

    Solo se pisan los campos que deciden QUE se descarga -- tag, sha256,
    files, version. La descripcion, el nombre y si es descargable siguen
    saliendo de la imagen: son cosa del core, no del registro de versiones.
    """
    remoto = fetch(force=force)
    fusionado: dict[str, dict] = {}
    for slug, meta in base.items():
        entrada = dict(meta)
        r = remoto.get(slug)
        if r and meta.get("downloadable"):
            for campo in ("tag", "sha256", "files", "version"):
                if r.get(campo):
                    entrada[campo] = r[campo]
            if r.get("tag") != meta.get("tag"):
                log.info(
                    "Plugin '%s': el manifiesto pinea %s (la imagen traia %s)",
                    slug, r.get("tag"), meta.get("tag"),
                )
        fusionado[slug] = entrada
    return fusionado
