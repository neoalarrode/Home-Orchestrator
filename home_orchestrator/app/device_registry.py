"""
Catalogo compartido de dispositivos consumibles INTERNAMENTE por otros
plugins (Tuya/Shelly/TP-Link/Govee hoy, cualquier otra marca mañana),
sin pasar por HA/MQTT -- ver `light_handle`/`climate_handle` en cada
plugin de ingesta.

Antes de este modulo, cada plugin CONSUMIDOR (Climate, Lighting) tenia su
propia copia casi identica de este mecanismo (`_actuator_providers`,
`register_actuator_provider`, `resolve_bridge_handle`), y cada plugin
PROVEEDOR tenia que exponer un metodo distinto por capacidad
(`light_handle`/`list_light_actuators`, `climate_handle`/
`list_climate_actuators`, ...). Sin ningun sitio central, anadir una
capacidad nueva (p.ej. "vacuum") a un consumidor nuevo obligaba a
duplicar TODO el mecanismo otra vez.

Aqui vive UNA sola vez, sin mencionar ninguna tecnologia ni ningun
plugin en concreto:
  - Un proveedor (Tuya, Shelly, TP-Link, Govee...) se registra UNA vez
    con `register_provider(prefix, provider)`. `provider` debe exponer:
      - `list_actuators(capability: str) -> list[{"ref", "name", "brand"}]`
      - `get_handle(capability: str, device_id: str, index: int) -> handle | None`
    despachando el, internamente, segun la capacidad pedida
    ("light"/"climate"/"vacuum"/...) -- el registro nunca sabe que hay
    dentro de cada handle, es opaco para el, solo lo pasa tal cual al
    consumidor que lo pidio.
  - Un consumidor (Climate, Lighting...) pide `list_actuators("light")`
    o `resolve(ref, "climate")` filtrando SIEMPRE por su propia
    capacidad -- nunca puede aparecer un actuador de otra capacidad en
    su selector, ni resolver un ref que no sea de la suya.

El formato de referencia (`<prefix>:<device_id>[:<indice>]`, ya guardado
en zonas/reglas existentes) NO cambia -- esto es una reorganizacion
interna, no una migracion de datos.
"""

from __future__ import annotations

import logging

log = logging.getLogger("device_registry")

_providers: dict[str, object] = {}


def register_provider(prefix: str, provider) -> None:
    _providers[prefix] = provider
    log.info("Registrado proveedor de dispositivos '%s'", prefix)


def get_provider(prefix: str):
    """Acceso directo a un proveedor por prefijo -- para capacidades
    OPCIONALES fuera del contrato generico (hoy: `get_actuator_history`,
    que solo tiene sentido para "climate" y solo Tuya lo implementa por
    ahora). None si ese prefijo no tiene proveedor registrado."""
    return _providers.get(prefix)


def is_bridge_ref(ref: str) -> bool:
    return bool(ref) and ":" in ref and ref.split(":", 1)[0] in _providers


def list_actuators(capability: str) -> list[dict]:
    """Agrega `list_actuators(capability)` de TODOS los proveedores
    registrados que la implementen -- un proveedor sin nada de esa
    capacidad (p.ej. Shelly para "climate") simplemente no aporta nada,
    nunca un error."""
    out: list[dict] = []
    for prefix, provider in _providers.items():
        lister = getattr(provider, "list_actuators", None)
        if lister is None:
            continue
        try:
            out.extend(lister(capability))
        except Exception:
            log.exception("Fallo listando actuadores '%s' del proveedor '%s'", capability, prefix)
    return out


def resolve(ref: str, capability: str):
    """`ref` = '<prefijo>:<device_id>[:<indice>]' -> handle, o None si el
    prefijo no tiene proveedor registrado ahora mismo, o si esa
    capacidad no aplica a esta referencia (una zona de Climate no puede
    resolver por error el ref de una luz, ni al reves)."""
    if ":" not in ref:
        return None
    prefix, rest = ref.split(":", 1)
    provider = _providers.get(prefix)
    if provider is None:
        return None
    getter = getattr(provider, "get_handle", None)
    if getter is None:
        return None
    parts = rest.split(":", 1)
    device_id = parts[0]
    index = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    try:
        return getter(capability, device_id, index)
    except Exception:
        return None
