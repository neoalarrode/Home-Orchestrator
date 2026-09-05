"""
Motor de decision de una zona de Covers -- mismo espiritu "sin caja
negra" que climate/zone_runner.py y lighting/zone_runner.py: todo lo que
decide se explica con la propia config de la zona y la posicion REAL del
sol (`sun.sun`, los mismos atributos `elevation`/`azimuth` que HA ya
calcula), nunca con una hora fija ni con un modelo oculto.

Tres reglas independientes, evaluadas en este orden de prioridad (la
primera que aplique manda, no se combinan):
  1. Proteccion solar: el sol da DIRECTO a esta ventana ahora mismo
     (azimut del sol dentro del rango que declara la orientacion de la
     ventana, elevacion por encima del minimo) -> baja a una posicion
     parcial (`sun_protection_position_pct`), no necesariamente del
     todo.
  2. Cierre nocturno (privacidad): se ha puesto el sol -> cierra del
     todo.
  3. Apertura diurna: ha amanecido y ninguna de las dos anteriores
     aplica -> abre del todo.

Sin ninguna regla activada (todo desactivado por defecto, ver
zone_store.py), la zona no toca nada -- igual que Lighting/Climate, una
zona nueva nunca cambia el comportamiento de una persiana hasta que el
usuario activa algo a proposito.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger("covers.zone_runner")

# Tolerancia para decidir si la posicion REAL de una persiana coincide con
# lo ultimo que le mandamos, o si alguien la ha tocado a mano por su
# cuenta -- algunos motores no paran exactamente en el % pedido.
POSITION_TOLERANCE_PCT = 4

FULLY_OPEN = 100
FULLY_CLOSED = 0


def _azimuth_in_range(azimuth: float, lo: float, hi: float) -> bool:
    """`lo <= azimuth <= hi`, salvo que el rango cruce el norte (0/360) --
    una ventana orientada al norte declara algo como `lo=350, hi=20`."""
    if lo <= hi:
        return lo <= azimuth <= hi
    return azimuth >= lo or azimuth <= hi


class ZoneRunner:
    def __init__(self, zone_id: str, cfg: dict, ws, state: dict | None = None) -> None:
        self.zone_id = zone_id
        self.zone = cfg
        self.ws = ws
        self._state = dict(state or {})

        # Snapshot en vivo para /api/zones -- ver climate/lighting.
        self.reason: str = "sin evaluar todavia"
        self.desired_position: int | None = None
        self.sun_position_ok: bool | None = None

    def to_persisted_state(self) -> dict:
        return dict(self._state)

    def watched_entities(self) -> set[str]:
        """`sun.sun` SIEMPRE (las tres reglas dependen de el), mas las
        propias persianas de la zona (para detectar si alguien las ha
        tocado a mano)."""
        out = {"sun.sun"}
        out |= {e for e in (self.zone.get("cover_entities") or []) if e}
        return out

    def all_covers(self) -> set[str]:
        return {e for e in (self.zone.get("cover_entities") or []) if e}

    # --------------------------------------------------------- lectura --

    def _current_position(self, states: dict[str, dict], entity_id: str) -> int | None:
        st = states.get(entity_id) or {}
        attrs = st.get("attributes") or {}
        pos = attrs.get("current_position")
        if pos is not None:
            return round(pos)
        # Sin `current_position` (persiana solo abierto/cerrado, sin
        # posicion intermedia) -- se aproxima por el propio `state`.
        state = st.get("state")
        if state == "open":
            return FULLY_OPEN
        if state == "closed":
            return FULLY_CLOSED
        return None

    def _supports_position(self, states: dict[str, dict], entity_id: str) -> bool:
        st = states.get(entity_id) or {}
        attrs = st.get("attributes") or {}
        # `SET_POSITION` = bit 4 (ver homeassistant.components.cover.CoverEntityFeature).
        features = attrs.get("supported_features") or 0
        return bool(features & 4)

    # ------------------------------------------------------- decision --

    def _sun_protection_active(self, cfg: dict, sun_attrs: dict) -> bool:
        if not cfg.get("sun_protection_enabled", False):
            return False
        elevation = sun_attrs.get("elevation")
        azimuth = sun_attrs.get("azimuth")
        if elevation is None or azimuth is None:
            return False
        min_elevation = float(cfg.get("sun_protection_min_elevation", 20) or 20)
        if elevation < min_elevation:
            return False
        lo = float(cfg.get("window_azimuth_min", 0) or 0)
        hi = float(cfg.get("window_azimuth_max", 360) or 360)
        return _azimuth_in_range(azimuth, lo, hi)

    def decide_and_act(self, states: dict[str, dict] | None = None) -> None:
        cfg = self.zone
        states = states if states is not None else self._snapshot_states()
        covers = self.all_covers()

        sun_state = states.get("sun.sun")
        if sun_state is None:
            self.reason = "sun.sun no disponible -> sin cambios"
            return
        sun_attrs = sun_state.get("attributes") or {}

        # Deteccion de "tocada a mano": si la posicion REAL ya no
        # coincide con lo ultimo que mandamos, se marca y se deja en paz
        # hasta la proxima transicion real de la zona -- mismo criterio
        # que Lighting/Climate.
        commanded = self._state.get("commanded") or {}
        overrides = self._state.setdefault("manual_override", {})
        respect_manual = cfg.get("respect_manual_changes", True)
        for entity_id in covers:
            cmd = commanded.get(entity_id)
            real = self._current_position(states, entity_id)
            if cmd is None or real is None:
                continue
            if abs(real - cmd) > POSITION_TOLERANCE_PCT:
                overrides[entity_id] = True

        sun_protection = self._sun_protection_active(cfg, sun_attrs)
        self.sun_position_ok = sun_protection
        is_daytime = sun_state.get("state") == "above_horizon"

        if sun_protection:
            desired = int(cfg.get("sun_protection_position_pct", 30) or 30)
            reason = f"proteccion solar activa (elevacion {sun_attrs.get('elevation')}°, azimut {sun_attrs.get('azimuth')}°) -> {desired}%"
        elif not is_daytime and cfg.get("night_close_enabled", False):
            desired = FULLY_CLOSED
            reason = "cierre nocturno -> 0% (cerrado)"
        elif is_daytime and cfg.get("day_open_enabled", False):
            desired = FULLY_OPEN
            reason = "apertura diurna -> 100% (abierto)"
        else:
            desired = None
            reason = "ninguna regla activa aplica ahora mismo -> sin cambios"

        self.desired_position = desired
        self.reason = reason

        if desired is None:
            return

        transitioned = self._state.get("last_desired") != desired
        self._state["last_desired"] = desired
        if not transitioned:
            return  # ya se aplico este mismo objetivo, no se repite cada ciclo

        for entity_id in covers:
            if respect_manual and overrides.get(entity_id):
                continue
            current = self._current_position(states, entity_id)
            if current is not None and abs(current - desired) <= POSITION_TOLERANCE_PCT:
                continue  # ya esta donde tiene que estar
            self._apply_position(states, entity_id, desired)
            overrides.pop(entity_id, None)

    def _apply_position(self, states: dict[str, dict], entity_id: str, position: int) -> None:
        try:
            if self._supports_position(states, entity_id):
                self.ws.call_service(
                    "cover", "set_cover_position",
                    service_data={"position": position}, target={"entity_id": entity_id},
                )
            elif position >= 50:
                self.ws.call_service("cover", "open_cover", target={"entity_id": entity_id})
            else:
                self.ws.call_service("cover", "close_cover", target={"entity_id": entity_id})
        except Exception:
            log.exception("Zona covers %s: fallo moviendo %s a %s%%", self.zone_id, entity_id, position)
            return
        self._state.setdefault("commanded", {})[entity_id] = position

    def _snapshot_states(self) -> dict[str, dict]:
        try:
            return {s.get("entity_id"): s for s in self.ws.get_states() if s.get("entity_id")}
        except Exception:
            log.exception("Zona covers %s: fallo leyendo estados de HA", self.zone_id)
            return {}

    # -------------------------------------------------- reactivo/periodico -

    def handle_reactive_event(self, states: dict[str, dict] | None = None) -> None:
        self.decide_and_act(states)

    def handle_periodic_reapply(self) -> None:
        self.decide_and_act()
