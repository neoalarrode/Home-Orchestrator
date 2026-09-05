"""
Motor de decision de una zona de Covers -- mismo espiritu "sin caja
negra" que climate/zone_runner.py y lighting/zone_runner.py: todo lo que
decide se explica con la propia config de la zona y la posicion REAL del
sol (`sun.sun`, los mismos atributos `elevation`/`azimuth` que HA ya
calcula), nunca con una hora fija ni con un modelo oculto.

Cuatro reglas independientes, evaluadas en este orden de prioridad (la
primera que aplique manda, no se combinan):
  1. Proteccion solar (termica): el sol da DIRECTO a esta ventana ahora
     mismo (azimut del sol dentro del rango que declara la orientacion
     de la ventana, elevacion por encima del minimo) -> baja a
     `sun_protection_position_pct` -- BINARIO por defecto (0%, cerrado
     del todo), a peticion expresa del usuario: "abro del todo y me
     cierro solo si el sol me pega, si hace falta". Si la zona declara
     un `climate_entity` vinculado (un `climate.*` de Climate
     Orchestrator), esta proteccion se SALTA cuando esa zona esta
     pidiendo calor ahora mismo -- el sol es calefaccion gratis en
     invierno, no algo de lo que protegerse (ver
     `_climate_wants_heat`). Sin vinculo declarado, se comporta igual
     que siempre (protege sin mirar Climate).
  2. Cierre nocturno (privacidad): se ha puesto el sol -> cierra del
     todo.
  3. Ritmo diario (`day_rhythm_*`): caso de uso DISTINTO de la
     proteccion solar, no una variante suya -- en vez de un salto
     binario ligado a si el sol da directo a la ventana, sigue una
     curva SUAVE a lo largo de todo el dia (se abre progresivamente de
     amanecer a mediodia solar, se cierra progresivamente de mediodia a
     atardecer -- ver `covers/schedule.py`, misma forma que la curva de
     brillo de Lighting).
  4. Apertura diurna: ha amanecido y ninguna de las anteriores aplica ->
     abre del todo.

Sin ninguna regla activada (todo desactivado por defecto, ver
zone_store.py), la zona no toca nada -- igual que Lighting/Climate, una
zona nueva nunca cambia el comportamiento de una persiana hasta que el
usuario activa algo a proposito.
"""

from __future__ import annotations

import logging
import time

from covers import schedule

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
        """`sun.sun` SIEMPRE (las cuatro reglas dependen de el), mas las
        propias persianas de la zona (para detectar si alguien las ha
        tocado a mano), mas el `climate_entity` vinculado si lo hay (un
        cambio de modo calor/frio en esa zona tiene que reevaluar la
        proteccion solar al instante, no esperar al ciclo periodico)."""
        out = {"sun.sun"}
        out |= {e for e in (self.zone.get("cover_entities") or []) if e}
        climate_entity = self.zone.get("climate_entity")
        if climate_entity:
            out.add(climate_entity)
        return out

    def all_covers(self) -> set[str]:
        return {e for e in (self.zone.get("cover_entities") or []) if e}

    # --------------------------------------------------------- lectura --

    def _current_position(self, states: dict[str, dict], entity_id: str) -> int | None:
        st = states.get(entity_id) or {}
        attrs = st.get("attributes") or {}
        pos = attrs.get("current_position")
        if pos is not None:
            pos = round(pos)
            # `invert_position` (ver zone_store.py): el motor de decision
            # SIEMPRE razona en la convencion normal (0=cerrada,
            # 100=abierta) -- si el dispositivo real reporta al reves, se
            # invierte AQUI, en el unico punto de lectura, para que el
            # resto del codigo nunca tenga que saberlo.
            if self.zone.get("invert_position", False):
                pos = FULLY_OPEN - pos
            return pos
        # Sin `current_position` (persiana solo abierto/cerrado, sin
        # posicion intermedia) -- se aproxima por el propio `state`, que
        # HA ya normaliza el solo (open/closed son semanticos, no un
        # numero que se pueda invertir).
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

    def _climate_wants_heat(self, states: dict[str, dict], cfg: dict) -> bool:
        """True si la zona de Climate vinculada esta pidiendo calor AHORA
        MISMO -- en ese caso el sol es calefaccion gratis (invierno), no
        algo de lo que protegerse, aunque de doble a la ventana. Sin
        `climate_entity` declarado, o si esa entidad no se puede leer,
        se responde False (se comporta como si no hubiera vinculo --
        protege con normalidad, mismo criterio de "sin dato, no se
        bloquea" que el resto del motor).

        `hvac_action` (lo que el equipo esta haciendo AHORA, "heating"/
        "cooling"/"idle"/"off") manda si esta disponible -- es la señal
        mas directa. Sin ella (o en "idle", ambiguo), se cae al
        `hvac_mode` general de la zona (el propio `state` de la entidad
        `climate.*`): "heat" cuenta como pidiendo calor aunque este
        idle ahora mismo (confort en modo calor implica querer
        calidez); "heat_cool"/"cool"/"off"/etc. no."""
        climate_entity = cfg.get("climate_entity")
        if not climate_entity:
            return False
        st = states.get(climate_entity)
        if st is None:
            return False
        attrs = st.get("attributes") or {}
        if attrs.get("hvac_action") == "heating":
            return True
        if attrs.get("hvac_action") == "cooling":
            return False
        return st.get("state") == "heat"

    def _forecast_heat_risk(self, states: dict[str, dict], cfg: dict) -> float | None:
        """[0, 1]: cuanto de fuerte es la subida de temperatura exterior
        PREVISTA para las proximas `sun_protection_forecast_lookahead_
        hours`, respecto al umbral `sun_protection_forecast_rise_
        threshold_deg` -- 0 si la subida prevista no llega al umbral, 1
        si lo alcanza o supera. `None` (no "sin subida", sino "no hay
        prevision que leer") si no hay `climate_entity` vinculado, esa
        entidad no se puede leer, o no expone `outdoor_now`/
        `outdoor_forecast` -- quien llama usa `None` para caer al
        comportamiento binario de siempre, nunca lo confunde con "0
        subida prevista de verdad"."""
        climate_entity = cfg.get("climate_entity")
        if not climate_entity:
            return None
        st = states.get(climate_entity)
        if st is None:
            return None
        attrs = st.get("attributes") or {}
        outdoor_now = attrs.get("outdoor_now")
        forecast = attrs.get("outdoor_forecast")
        if outdoor_now is None or not forecast:
            return None
        lookahead = int(cfg.get("sun_protection_forecast_lookahead_hours", 3) or 3)
        window = forecast[:lookahead]
        if not window:
            return None
        rise = max(window) - outdoor_now
        threshold = float(cfg.get("sun_protection_forecast_rise_threshold_deg", 3) or 3)
        if threshold <= 0:
            return None
        return max(0.0, min(1.0, rise / threshold))

    def _sun_protection_position(self, cfg: dict, sun_attrs: dict, states: dict[str, dict]) -> int | None:
        """Posicion que pide la proteccion solar ahora mismo, o `None` si
        no aplica en absoluto (fuera de la ventana de azimut, de noche,
        o la zona de Climate vinculada esta pidiendo calor -- el sol es
        calefaccion gratis, no algo de lo que protegerse).

        Con `climate_entity` vinculado Y `outdoor_forecast` legible: la
        posicion es GRADUAL -- combina cuanto se acerca la elevacion
        real al umbral configurado (0 lejos, 1 en el umbral o mas alla)
        con la fuerza de la subida de temperatura prevista
        (`_forecast_heat_risk`), y solo entonces interpola entre
        FULLY_OPEN y `sun_protection_position_pct`. Sin eso legible, se
        comporta igual que siempre: binario, de golpe al cruzar
        `sun_protection_min_elevation`."""
        if not cfg.get("sun_protection_enabled", False):
            return None
        elevation = sun_attrs.get("elevation")
        azimuth = sun_attrs.get("azimuth")
        if elevation is None or azimuth is None:
            return None
        if elevation <= 0:
            return None  # de noche/al ras del horizonte, nunca hay nada que proteger
        lo = float(cfg.get("window_azimuth_min", 0) or 0)
        hi = float(cfg.get("window_azimuth_max", 360) or 360)
        if not _azimuth_in_range(azimuth, lo, hi):
            return None
        if self._climate_wants_heat(states, cfg):
            return None

        target = int(cfg.get("sun_protection_position_pct", 0) or 0)
        min_elevation = float(cfg.get("sun_protection_min_elevation", 20) or 20)
        heat_risk = self._forecast_heat_risk(states, cfg)
        if heat_risk is None:
            if elevation < min_elevation:
                return None
            return target

        elevation_factor = max(0.0, min(1.0, elevation / min_elevation)) if min_elevation > 0 else 1.0
        factor = elevation_factor * heat_risk
        if factor <= 0:
            return None
        return round(FULLY_OPEN - factor * (FULLY_OPEN - target))

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

        sun_protection_position = self._sun_protection_position(cfg, sun_attrs, states)
        self.sun_position_ok = sun_protection_position is not None
        is_daytime = sun_state.get("state") == "above_horizon"

        if sun_protection_position is not None:
            desired = sun_protection_position
            reason = f"proteccion solar activa (elevacion {sun_attrs.get('elevation')}°, azimut {sun_attrs.get('azimuth')}°) -> {desired}%"
        elif not is_daytime and cfg.get("night_close_enabled", False):
            desired = FULLY_CLOSED
            reason = "cierre nocturno -> 0% (cerrado)"
        elif cfg.get("day_rhythm_enabled", False) and (pos := schedule.sun_position(sun_attrs)) is not None:
            min_pct = float(cfg.get("day_rhythm_min_position_pct", 0) or 0)
            max_pct = float(cfg.get("day_rhythm_max_position_pct", 100) or 100)
            desired = schedule.day_rhythm_position(pos, min_pct, max_pct)
            reason = f"ritmo diario ({round(pos, 2)}) -> {desired}%"
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
        raw_position = FULLY_OPEN - position if self.zone.get("invert_position", False) else position
        try:
            if self._supports_position(states, entity_id):
                self.ws.call_service(
                    "cover", "set_cover_position",
                    service_data={"position": raw_position}, target={"entity_id": entity_id},
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
