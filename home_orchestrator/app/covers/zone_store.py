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
    # Por defecto, binario (0% = cerrado del todo) -- a peticion expresa
    # del usuario, no una posicion intermedia arbitraria. Sigue siendo un
    # numero editable (0-100) si alguien de verdad quiere un cierre
    # parcial, pero el punto de partida es el mismo "abierto o cerrado"
    # que ya usan cierre nocturno/apertura diurna.
    "sun_protection_position_pct": 0,
    # Vinculo opcional con una zona de Climate (el propio `climate.*` que
    # publica ese plugin, p.ej. "climate.salon") -- a peticion expresa
    # del usuario: sin esto, la proteccion solar bloquearia el sol
    # tambien en invierno, cuando esa misma zona podria estar pidiendo
    # calor y el sol es calefaccion gratis. Con el vinculo declarado, la
    # proteccion solar SOLO actua si esa zona no esta pidiendo calor
    # ahora mismo (ver ZoneRunner._climate_wants_heat) -- sin vinculo,
    # se comporta igual que antes (protege siempre que el sol de
    # directo, sin mirar Climate).
    "climate_entity": "",
    # Previsión (opcional, solo tiene efecto CON `climate_entity`
    # vinculado -- sin el, no hay prevision que leer): en vez de un
    # salto binario justo al cruzar `sun_protection_min_elevation`, si
    # la zona de Climate vinculada expone `outdoor_forecast` (ya lo
    # publica, ver climate/zone_forecast.py) y esta prevee una subida
    # de temperatura exterior significativa en las proximas horas, la
    # persiana empieza a cerrarse GRADUALMENTE desde antes -- cuanto
    # mas cerca este la elevacion del sol del umbral Y mas fuerte sea
    # la subida prevista, mas se cierra (ver
    # ZoneRunner._sun_protection_position). Sin `outdoor_forecast`
    # legible (o sin `climate_entity`), se comporta igual que siempre:
    # binario, de golpe al cruzar el umbral.
    "sun_protection_forecast_lookahead_hours": 3,
    "sun_protection_forecast_rise_threshold_deg": 3,
    # Ritmo diario (opcional, DESACTIVADO por defecto) -- caso de uso
    # DISTINTO de la proteccion solar, no una variante suya: en vez de un
    # salto binario ligado a si el sol da directo a la ventana, la
    # persiana sigue una curva SUAVE a lo largo de todo el dia -- se va
    # abriendo progresivamente desde el amanecer hasta el mediodia solar
    # (maxima apertura) y se va cerrando progresivamente desde el
    # mediodia hasta el atardecer. Misma forma de curva que ya usa
    # Lighting para el brillo (`lighting/schedule.py`, sube hasta el
    # mediodia solar, baja despues) -- ver `covers/schedule.py`, propia
    # de este plugin para no acoplarse al paquete de Lighting.
    "day_rhythm_enabled": False,
    "day_rhythm_min_position_pct": 0,
    "day_rhythm_max_position_pct": 100,
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
