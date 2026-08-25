"""
Identificacion de dispositivos que NO se anuncian en la LAN.

Cierra el ultimo hueco del descubrimiento. La cadena era esta:

- El descubrimiento pasivo (discovery.py) solo oye a quien se anuncia por
  broadcast UDP. Hay motivos de sobra para que ese anuncio no llegue --
  aislamiento de clientes en el punto de acceso, otra VLAN, un sistema mesh
  que no reenvia broadcast, o un dispositivo que solo se anuncia al arrancar.
- El barrido activo (`discovery.active_scan`) encuentra la IP, pero un
  connect TCP al puerto de datos NO dice QUE hay ahi: ni device_id ni
  version de protocolo.
- La cuenta de la nube (`tuya_cloud.get_user_devices`) si sabe el device_id,
  el nombre y el local_key de todo... pero no la IP local.

Ninguna de las tres piezas sirve sola. Este modulo las cruza, y lo hace de
la unica forma que es PRUEBA y no conjetura: el `local_key` es por
dispositivo, asi que si el handshake contra una IP concreta funciona con la
clave de un dispositivo concreto, esa IP ES ese dispositivo. De paso queda
determinada la version de protocolo, que es justo el otro dato que el
barrido no puede saber -- y sin el, el usuario tendria que adivinarla.

Verificado contra hardware real: un robot aspirador que nunca se habia
anunciado quedo identificado en su IP y con su version (3.5) correcta, sin
que el usuario tuviera que cruzar nada a mano.
"""

from __future__ import annotations

import asyncio
import logging

from .discovery import DiscoveredDevice, active_scan
from .tuya_lan import TuyaLocalDevice

_LOGGER = logging.getLogger(__name__)

# Orden deliberado: las mas frecuentes primero, para acertar en el primer
# intento en el caso normal. 3.2 va al final porque arranca en el dialecto
# type_0d y su consulta necesita la lista explicita de DPS, que aqui todavia
# no se conoce -- probarla antes solo gastaria intentos.
PROBE_VERSIONS = ("3.3", "3.5", "3.4", "3.52", "3.42", "3.22", "3.1", "3.2")

CONNECT_TIMEOUT = 6.0
STATUS_TIMEOUT = 6.0


async def _probe(device_id: str, local_key: str, ip: str, version: str) -> bool:
    """True si `ip` responde de verdad como `device_id` hablando `version`."""
    device = TuyaLocalDevice(device_id, ip, local_key, version)
    try:
        await asyncio.wait_for(device.connect(timeout=CONNECT_TIMEOUT, retries=1), CONNECT_TIMEOUT + 2)
    except Exception:
        return False
    try:
        # Conectar no basta como prueba: el handshake de 3.3 no autentica
        # nada (no hay clave de sesion que negociar), asi que un connect
        # "correcto" contra el dispositivo equivocado es perfectamente
        # posible. Lo que descarta un falso positivo es que devuelva DPS
        # descifrables -- eso si depende del local_key.
        dps = await asyncio.wait_for(device.status(), STATUS_TIMEOUT)
        return bool(dps)
    except Exception:
        return False
    finally:
        try:
            await device.close()
        except Exception:
            pass


async def identify(
    candidates: list[dict],
    known_ips: set[str],
    scan_timeout: float | None = None,
) -> list[DiscoveredDevice]:
    """Cruza un barrido activo con `candidates` y devuelve los identificados.

    `candidates`: dicts con device_id / local_key / name / product_id -- tal
    y como los da `tuya_cloud.get_user_devices`, ya filtrados a los que
    interesa buscar (los que no estan dados de alta ni se han oido).
    `known_ips`: IPs que ya se sabe de quien son; no se prueban.

    Devuelve `DiscoveredDevice` -- la MISMA forma que el descubrimiento
    pasivo, a proposito: rio arriba estos aparecen en la lista de detectados
    junto a los demas, sin que el usuario tenga que saber que a unos se les
    oyo y a otros hubo que buscarlos.
    """
    usable = [c for c in candidates if c.get("local_key") and c.get("device_id")]
    if not usable:
        return []

    ips = [ip for ip in await active_scan(timeout=scan_timeout or 0.6) if ip not in known_ips]
    if not ips:
        _LOGGER.debug("Tuya: el barrido no ha dejado ninguna IP sin identificar")
        return []

    _LOGGER.info(
        "Tuya: %d IP(s) sin identificar y %d dispositivo(s) de la cuenta por localizar "
        "-- probando cual es cual",
        len(ips), len(usable),
    )

    found: list[DiscoveredDevice] = []
    pendientes = list(usable)
    for ip in ips:
        for candidate in list(pendientes):
            matched_version = None
            for version in PROBE_VERSIONS:
                if await _probe(candidate["device_id"], candidate["local_key"], ip, version):
                    matched_version = version
                    break
            if matched_version is None:
                continue
            _LOGGER.info(
                "Tuya: %s identificado como «%s» (%s), protocolo %s",
                ip, candidate.get("name") or candidate["device_id"],
                candidate["device_id"], matched_version,
            )
            found.append(DiscoveredDevice(
                device_id=candidate["device_id"],
                ip=ip,
                product_key=candidate.get("product_id"),
                version=matched_version,
                name=candidate.get("name"),
            ))
            # Un dispositivo esta en UNA sola IP: ni se vuelve a probar, ni
            # se sigue probando esta IP con otros candidatos.
            pendientes.remove(candidate)
            break
    return found
