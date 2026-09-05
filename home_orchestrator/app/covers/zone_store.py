"""
Persistencia de las zonas de Covers -- mismo fichero de config compartido
del nucleo, bajo su propio namespace "plugins.covers" (nunca pisa
"plugins.lighting" ni "plugins.climate"). Calcado deliberadamente de
lighting/zone_store.py -- mismo patron ya probado en produccion.
"""

from __future__ import annotations

import uuid

import config_store

PLUGIN_KEY = "covers"


DEFAULT_ZONE_CONFIG = {
    "name": "",
    # `cover.*` de HA -- una zona puede agrupar varias persianas de la
    # misma orientacion (p.ej. dos ventanas del mismo salon) para que se
    # muevan juntas.
    "cover_entities": [],
    "respect_manual_changes": True,
    "reapply_minutes": 5,
    # Cierre nocturno (privacidad) / apertura diurna -- independientes
    # entre si, cada una desactivada por defecto para no tocar ninguna
    # persiana existente hasta que el usuario lo active a proposito.
    "night_close_enabled": False,
    "day_open_enabled": False,
    # Proteccion solar: si el sol da DIRECTO a esta ventana (posicion
    # real del sol, sun.sun -- azimut dentro del rango que declara la
    # orientacion de la ventana, y elevacion por encima del minimo), la
    # persiana baja a `sun_protection_position_pct` -- NO
    # necesariamente del todo, solo lo justo para cortar el sol directo
    # sin dejar la habitacion a oscuras. Se apoya en los mismos
    # atributos de sun.sun (elevation/azimuth) que HA ya calcula, sin
    # depender de ninguna libreria externa ni de otro plugin.
    "sun_protection_enabled": False,
    "window_azimuth_min": 90,
    "window_azimuth_max": 270,
    "sun_protection_min_elevation": 20,
    "sun_protection_position_pct": 30,
}


def _read_covers_section() -> dict:
    return config_store.read_plugin_section(PLUGIN_KEY, {"zones": []})


def _write_covers_section(section: dict) -> None:
    config_store.update_plugin_section(PLUGIN_KEY, section)


def load_zones() -> list[dict]:
    """Lista de zonas, cada una `{"id", "config", "state"}`."""
    with config_store.transaction():
        section = _read_covers_section()
        zones = section.get("zones") or []
        for z in zones:
            merged = dict(DEFAULT_ZONE_CONFIG)
            merged.update(z.get("config") or {})
            z["config"] = merged
            z.setdefault("state", {})
        return zones


def save_zones(zones: list[dict]) -> None:
    with config_store.transaction():
        _write_covers_section({"zones": zones})


def add_zone(config: dict) -> dict:
    with config_store.transaction():
        zones = load_zones()
        merged = dict(DEFAULT_ZONE_CONFIG)
        merged.update(config)
        zone = {"id": str(uuid.uuid4())[:8], "config": merged, "state": {}}
        zones.append(zone)
        save_zones(zones)
        return zone


def update_zone_config(zone_id: str, config: dict) -> dict | None:
    with config_store.transaction():
        zones = load_zones()
        for z in zones:
            if z["id"] == zone_id:
                z["config"].update(config)
                save_zones(zones)
                return z
        return None


def update_zone_state(zone_id: str, state: dict) -> None:
    with config_store.transaction():
        zones = load_zones()
        for z in zones:
            if z["id"] == zone_id:
                z["state"] = state
                save_zones(zones)
                return


def delete_zone(zone_id: str) -> bool:
    with config_store.transaction():
        zones = load_zones()
        before = len(zones)
        zones = [z for z in zones if z["id"] != zone_id]
        save_zones(zones)
        return len(zones) < before
