"""
Expone un dispositivo Tuya a Home Assistant via MQTT Discovery -- OPCIONAL
y por dispositivo (no todo lo que se ingesta tiene que publicarse: un
termostato consumido internamente por Climate no necesita aparecer aqui
tambien, ver tuya_plugin.py). Genera un dominio MQTT distinto por cada
`dps:` del perfil segun su `platform` (switch/sensor/number/binary_sensor
/select) -- no solo climates, la mayoria de dispositivos Tuya no son
termostatos. Las entidades `climates:` del perfil se publican como
`climate.*` nativo, mismo mecanismo que ya usa mqtt_climate.py para
Climate Orchestrator.

Un `MqttTuyaDevice` por dispositivo. El `ha_mqtt.HAMqttClient` es
compartido entre todos los dispositivos del plugin -- una sola conexion
al broker, no una por dispositivo (mismo criterio que Climate).
"""

from __future__ import annotations

import json
import logging
from functools import partial

import ha_mqtt
from tuya.profile import (
    LIGHT_MAX_MIREDS,
    LIGHT_MIN_MIREDS,
    decode_color_hs,
    encode_color_hs,
    light_dp_to_mireds,
    mireds_to_light_dp,
)

log = logging.getLogger("tuya.mqtt")

DISCOVERY_PREFIX = "homeassistant"
NODE_ID = "home_orchestrator_tuya"

# platform (DPMapping) -> dominio MQTT Discovery. Nombres iguales a
# proposito -- no hace falta traducir, HA usa los mismos.
_DOMAIN_FOR_PLATFORM = {
    "switch": "switch",
    "sensor": "sensor",
    "number": "number",
    "binary_sensor": "binary_sensor",
    "select": "select",
}

# Vocabulario de estados que admite una entidad `vacuum.*` de HA. Fuera de
# estos la tarjeta no sabe que pintar, asi que lo que no encaje se cae a
# "idle" en vez de publicarse tal cual (ver `_vacuum_activity`).
_VACUUM_ACTIVITIES = frozenset({"cleaning", "docked", "paused", "idle", "returning", "error"})

# Las UNICAS capacidades que admite el discovery MQTT de un vacuum en HA
# (`STRING_TO_SERVICE` en homeassistant/components/mqtt/vacuum.py).
#
# BUG REAL, y de los caros de diagnosticar: HA valida esta lista con
# `vol.In(...)`, asi que UN SOLO valor no reconocido no se ignora -- tumba el
# mensaje de discovery ENTERO y la entidad no llega a existir. No aparece a
# medias: no aparece. Se colaron dos:
#   - "state", que no es una capacidad sino el esquema (va en `schema`).
#   - "battery", que HA retiro de las capacidades de vacuum.
# `battery_level` se sigue publicando en el estado; si la version de HA ya no
# lo usa simplemente lo ignora, que es inofensivo -- al reves no lo era.
_VACUUM_FEATURES = frozenset({
    "start", "pause", "stop", "return_home", "fan_speed",
    "status", "send_command", "locate", "clean_spot",
})


class MqttTuyaDevice:
    def __init__(self, mqtt_client, manager, device_id: str, device_name: str) -> None:
        self._mqtt = mqtt_client
        self._manager = manager
        self.device_id = device_id
        self.device_name = device_name or device_id
        self._device_block = {
            "identifiers": [f"{NODE_ID}_{device_id}"],
            "name": self.device_name,
            "manufacturer": "Tuya",
            "model": self.device_name,
        }
        # Comandos fuera del hilo de red de paho + publicacion del estado en
        # cuanto se aplican (ver ha_mqtt.MqttCommandWorker).
        self._commands = ha_mqtt.MqttCommandWorker(
            name=f"tuya-mqtt-cmd-{device_id}", on_done=self.publish_state,
        )
        # Estados de aspirador que el perfil no traduce: se avisa UNA vez por
        # valor, no en cada ciclo (ver `_vacuum_activity`).
        self._vacuum_unknown_states: set[str] = set()

    def _base(self, suffix: str) -> str:
        return f"{DISCOVERY_PREFIX}/{{domain}}/{NODE_ID}/{self.device_id}_{suffix}"

    # ---------------------------------------------------------- discovery -

    def publish_discovery(self) -> None:
        profile = self._manager.profile(self.device_id)
        if profile is None:
            log.warning("Tuya %s: sin perfil, no se publica nada por MQTT", self.device_id)
            return
        for dp in profile.dps:
            domain = _DOMAIN_FOR_PLATFORM.get(dp.platform)
            if domain:
                self._publish_dp(domain, dp)
        for i, cm in enumerate(profile.climates):
            self._publish_climate(i, cm)
        for i, lt in enumerate(profile.lights):
            self._publish_light(i, lt)
        # GAP CERRADO AQUI: el perfil parseaba `vacuums:` desde siempre y
        # `auto_profile` sabia construirlo (start/pause/return/locate/bateria/
        # estado/velocidad), pero aqui no se publicaba NADA -- un robot
        # aspirador se daba de alta correctamente y no aparecia ninguna
        # entidad `vacuum.*` en HA. Confirmado en produccion.
        for i, vm in enumerate(profile.vacuums):
            self._publish_vacuum(i, vm)

    def _publish_vacuum(self, index: int, vm) -> None:
        """Publica el bloque `vacuums:` como una entidad `vacuum.*` nativa
        (esquema `state` de HA -- el `legacy` esta retirado).

        `supported_features` se declara segun lo que el dispositivo ofrece de
        verdad, no una lista fija: anunciar un boton que luego no hace nada
        es peor que no tenerlo.
        """
        base = self._base(f"vacuum{index}").format(domain="vacuum")
        features: list[str] = []
        payload = {
            "name": vm.name,
            "unique_id": f"{NODE_ID}_{self.device_id}_vacuum{index}",
            "schema": "state",
            "state_topic": f"{base}/state",
            "command_topic": f"{base}/command",
            "availability_topic": f"{base}/availability",
            "device": self._device_block,
        }
        if vm.icon:
            payload["icon"] = vm.icon

        if vm.start_dp is not None:
            features += ["start", "pause"]
            payload.update(payload_start="start", payload_pause="pause")
            # Parar del todo solo se anuncia si hay un DP de arranque que
            # apagar; si no, "stop" no tendria a que traducirse.
            features.append("stop")
            payload["payload_stop"] = "stop"
        if vm.return_dp is not None:
            features.append("return_home")
            payload["payload_return_to_base"] = "return_to_base"
        if vm.locate_dp is not None:
            features.append("locate")
            payload["payload_locate"] = "locate"
        if vm.fan_speed_dp is not None and vm.fan_speed_map:
            features.append("fan_speed")
            payload["set_fan_speed_topic"] = f"{base}/fan_speed/set"
            payload["fan_speed_list"] = list(vm.fan_speed_map.values())
            self._mqtt.subscribe(
                f"{base}/fan_speed/set", partial(self._on_vacuum_fan_speed, index),
            )

        # Red de seguridad, porque el precio de equivocarse aqui es que la
        # entidad no exista y sin un mensaje que lo explique: se filtra contra
        # lo que HA admite de verdad y se avisa si algo se ha colado.
        invalidas = [f for f in features if f not in _VACUUM_FEATURES]
        if invalidas:
            log.error(
                "Tuya %s: capacidades de aspirador no validas para HA %s -- se descartan "
                "(dejarlas tumbaria el discovery entero)", self.device_id, invalidas,
            )
            features = [f for f in features if f in _VACUUM_FEATURES]
        payload["supported_features"] = features
        self._mqtt.subscribe(f"{base}/command", partial(self._on_vacuum_command, index))
        self._mqtt.publish(f"{base}/config", payload, retain=True)
        self._mqtt.publish(f"{base}/availability", "online", retain=True)

    def _publish_dp(self, domain: str, dp) -> None:
        base = self._base(f"dp{dp.dp_id}").format(domain=domain)
        payload = {
            "name": dp.name,
            "unique_id": f"{NODE_ID}_{self.device_id}_dp{dp.dp_id}",
            "state_topic": f"{base}/state",
            "availability_topic": f"{base}/availability",
            "device": self._device_block,
        }
        if dp.icon:
            payload["icon"] = dp.icon
        if dp.device_class:
            payload["device_class"] = dp.device_class
        if dp.unit:
            payload["unit_of_measurement"] = dp.unit

        if domain == "switch":
            payload.update(command_topic=f"{base}/set", payload_on="ON", payload_off="OFF", state_on="ON", state_off="OFF")
            self._mqtt.subscribe(f"{base}/set", partial(self._on_bool_command, dp))
        elif domain == "binary_sensor":
            payload.update(payload_on="ON", payload_off="OFF")
        elif domain == "number":
            payload["command_topic"] = f"{base}/set"
            if dp.min_value is not None:
                payload["min"] = dp.min_value
            if dp.max_value is not None:
                payload["max"] = dp.max_value
            if dp.step is not None:
                payload["step"] = dp.step
            self._mqtt.subscribe(f"{base}/set", partial(self._on_number_command, dp))
        elif domain == "select":
            payload["command_topic"] = f"{base}/set"
            payload["options"] = list((dp.value_map or {}).values())
            self._mqtt.subscribe(f"{base}/set", partial(self._on_select_command, dp))

        self._mqtt.publish(f"{base}/config", payload, retain=True)
        self._mqtt.publish(f"{base}/availability", "online", retain=True)

    def _publish_climate(self, index: int, cm) -> None:
        base = self._base(f"climate{index}").format(domain="climate")
        modes = ["off"]
        if cm.mode_dp is not None and cm.mode_map:
            modes = ["off", *sorted(set(cm.mode_map.values()))]
        elif cm.switch_dp is not None:
            modes = ["off", "heat"]
        payload = {
            "name": cm.name,
            "unique_id": f"{NODE_ID}_{self.device_id}_climate{index}",
            "modes": modes,
            "mode_state_topic": f"{base}/mode/state",
            "mode_command_topic": f"{base}/mode/set",
            "current_temperature_topic": f"{base}/current_temp/state",
            "availability_topic": f"{base}/availability",
            "device": self._device_block,
        }
        if cm.target_temp_dp is not None:
            payload.update(
                temperature_state_topic=f"{base}/temp/state",
                temperature_command_topic=f"{base}/temp/set",
                min_temp=cm.target_temp_min, max_temp=cm.target_temp_max, temp_step=cm.target_temp_step,
            )
            self._mqtt.subscribe(f"{base}/temp/set", partial(self._on_climate_temp, index))
        if cm.icon:
            payload["icon"] = cm.icon
        self._mqtt.subscribe(f"{base}/mode/set", partial(self._on_climate_mode, index))
        self._mqtt.publish(f"{base}/config", payload, retain=True)
        self._mqtt.publish(f"{base}/availability", "online", retain=True)

    def _publish_light(self, index: int, lt) -> None:
        """Publica el bloque `lights:` del perfil como una entidad
        `light.*` de verdad (encendido+brillo+color en una tarjeta), no
        como DPs sueltos -- antes esto no existia en absoluto: una
        bombilla nunca tenia una tarjeta de luz real, solo los sensores/
        switches sueltos de `dps:` (los DPs de brillo/color/modo de una
        bombilla NUNCA aparecen en `dps:` de todos modos -- el perfil los
        consume aqui, en `lights:`, precisamente para que no se dupliquen
        como dos entidades para lo mismo)."""
        base = self._base(f"light{index}").format(domain="light")
        payload = {
            "name": lt.name,
            "unique_id": f"{NODE_ID}_{self.device_id}_light{index}",
            "state_topic": f"{base}/state",
            "command_topic": f"{base}/set",
            "payload_on": "ON", "payload_off": "OFF",
            "availability_topic": f"{base}/availability",
            "device": self._device_block,
        }
        if lt.brightness_dp is not None:
            payload.update(
                brightness_state_topic=f"{base}/brightness/state",
                brightness_command_topic=f"{base}/brightness/set",
                brightness_scale=int(lt.brightness_max),
            )
            self._mqtt.subscribe(f"{base}/brightness/set", partial(self._on_light_brightness, index))
        if lt.color_temp_dp is not None:
            # BUG FIXED HERE: esto declaraba `min_mireds=1,
            # max_mireds=color_temp_max` -- tratando la escala CRUDA del
            # DP (0..color_temp_max, especifica del fabricante, NUNCA
            # mireds de verdad) como si YA fuera mireds. HA lo traducia a
            # limites sin sentido fisico (min/max_color_temp_kelvin =
            # 1.000.000K/1000K, confirmado en produccion) y el color real
            # aplicado no correspondia al pedido. Los limites correctos
            # (y la conversion de ida y vuelta, ver `_on_light_color_temp`/
            # `_publish_light_state`) viven en tuya/profile.py, en un solo
            # sitio para no divergir del control directo (TuyaLightHandle).
            payload.update(
                color_temp_state_topic=f"{base}/color_temp/state",
                color_temp_command_topic=f"{base}/color_temp/set",
                min_mireds=LIGHT_MIN_MIREDS, max_mireds=LIGHT_MAX_MIREDS,
            )
            self._mqtt.subscribe(f"{base}/color_temp/set", partial(self._on_light_color_temp, index))
        if lt.color_dp is not None:
            payload.update(hs_state_topic=f"{base}/hs/state", hs_command_topic=f"{base}/hs/set")
            self._mqtt.subscribe(f"{base}/hs/set", partial(self._on_light_hs, index))
        if lt.icon:
            payload["icon"] = lt.icon

        self._mqtt.subscribe(f"{base}/set", partial(self._on_light_power, index))
        self._mqtt.publish(f"{base}/config", payload, retain=True)
        self._mqtt.publish(f"{base}/availability", "online", retain=True)

    def remove_discovery(self) -> None:
        profile = self._manager.profile(self.device_id)
        if profile is None:
            return
        for dp in profile.dps:
            domain = _DOMAIN_FOR_PLATFORM.get(dp.platform)
            if domain:
                self._mqtt.publish(self._base(f"dp{dp.dp_id}").format(domain=domain) + "/config", "", retain=True)
        for i in range(len(profile.climates)):
            self._mqtt.publish(self._base(f"climate{i}").format(domain="climate") + "/config", "", retain=True)
        for i in range(len(profile.lights)):
            self._mqtt.publish(self._base(f"light{i}").format(domain="light") + "/config", "", retain=True)
        for i in range(len(profile.vacuums)):
            self._mqtt.publish(self._base(f"vacuum{i}").format(domain="vacuum") + "/config", "", retain=True)

    # -------------------------------------------------------------- estado

    def publish_state(self) -> None:
        """Llamar tras cualquier cambio conocido (on_any_change del
        device_manager) -- publica el valor actual de cada entidad
        expuesta. Simple y sin debounce: son solo unos pocos topics MQTT
        por dispositivo, no hace falta optimizar."""
        profile = self._manager.profile(self.device_id)
        if profile is None:
            return
        for dp in profile.dps:
            domain = _DOMAIN_FOR_PLATFORM.get(dp.platform)
            if not domain:
                continue
            value = self._manager.get_decoded(self.device_id, dp.dp_id)
            base = self._base(f"dp{dp.dp_id}").format(domain=domain)
            self._publish_availability(base)
            self._mqtt.publish(f"{base}/state", self._encode_state(domain, value), retain=True)
        for i, cm in enumerate(profile.climates):
            self._publish_climate_state(i, cm)
        for i, lt in enumerate(profile.lights):
            self._publish_light_state(i, lt)
        for i, vm in enumerate(profile.vacuums):
            self._publish_vacuum_state(i, vm)

    def _publish_vacuum_state(self, index: int, vm) -> None:
        base = self._base(f"vacuum{index}").format(domain="vacuum")
        self._publish_availability(base)
        state: dict = {"state": self._vacuum_activity(vm)}
        if vm.battery_dp is not None:
            battery = self._manager.get_decoded(self.device_id, vm.battery_dp)
            if battery is not None:
                try:
                    state["battery_level"] = int(float(battery) * (vm.battery_scale or 1))
                except (TypeError, ValueError):
                    pass
        if vm.fan_speed_dp is not None and vm.fan_speed_map:
            raw = self._manager.get_decoded(self.device_id, vm.fan_speed_dp)
            if raw is not None:
                state["fan_speed"] = vm.fan_speed_map.get(str(raw), str(raw))
        self._mqtt.publish(f"{base}/state", state, retain=True)

    def _vacuum_activity(self, vm) -> str:
        """Estado en el vocabulario de HA. Fuera de esos valores la tarjeta no
        sabe que pintar, asi que un estado que el mapa no cubre NO se pasa tal
        cual: se cae a `idle` y se avisa una vez.

        Pasa de verdad -- el mapa lo deduce `auto_profile` del esquema de la
        nube, y un robot con base de lavado tiene estados que ahi no salen
        (visto: `airing`, secando la mopa). Publicar eso deja la entidad en un
        estado invalido; decir "en reposo" es la verdad mas cercana.
        """
        if vm.status_dp is None:
            return "idle"
        raw = self._manager.get_decoded(self.device_id, vm.status_dp)
        if raw is None:
            return "idle"
        mapped = (vm.status_map or {}).get(str(raw))
        if mapped in _VACUUM_ACTIVITIES:
            return mapped
        if str(raw) not in self._vacuum_unknown_states:
            self._vacuum_unknown_states.add(str(raw))
            log.info(
                "Tuya %s: el aspirador reporta el estado %r, que su perfil no traduce "
                "-- se publica como 'idle'. Añade `%s: <estado>` a `status_map` si "
                "quieres que se vea de otra forma.",
                self.device_id, raw, raw,
            )
        return "idle"

    def _on_vacuum_command(self, index, client, userdata, msg) -> None:
        command = msg.payload.decode(errors="replace").strip()

        def apply() -> None:
            profile = self._manager.profile(self.device_id)
            if profile is None or index >= len(profile.vacuums):
                return
            vm = profile.vacuums[index]
            if command in ("start", "pause", "stop"):
                # `start_map` cubre los dispositivos cuyo DP de arranque es un
                # enum ("smart"/"pause") en vez de un booleano.
                if vm.start_map:
                    raw = vm.start_map.get(command)
                    if raw is None and command == "stop":
                        raw = vm.start_map.get("pause")
                    if raw is not None:
                        self._manager.set_dp(self.device_id, vm.start_dp, raw)
                    return
                if command == "pause" and vm.pause_dp is not None:
                    self._manager.set_dp(self.device_id, vm.pause_dp, True)
                elif vm.start_dp is not None:
                    self._manager.set_dp(self.device_id, vm.start_dp, command == "start")
            elif command == "return_to_base" and vm.return_dp is not None:
                self._manager.set_dp(self.device_id, vm.return_dp, True)
            elif command == "locate" and vm.locate_dp is not None:
                self._manager.set_dp(self.device_id, vm.locate_dp, True)
            else:
                log.warning(
                    "Tuya %s: el aspirador no admite la orden %r", self.device_id, command,
                )

        self._commands.submit(apply)

    def _on_vacuum_fan_speed(self, index, client, userdata, msg) -> None:
        label = msg.payload.decode(errors="replace").strip()

        def apply() -> None:
            profile = self._manager.profile(self.device_id)
            if profile is None or index >= len(profile.vacuums):
                return
            vm = profile.vacuums[index]
            raw = next((k for k, v in (vm.fan_speed_map or {}).items() if v == label), None)
            if raw is None:
                log.warning("Tuya %s: velocidad de aspirador desconocida %r", self.device_id, label)
                return
            self._manager.set_dp(self.device_id, vm.fan_speed_dp, raw)

        self._commands.submit(apply)

    def _publish_availability(self, base: str) -> None:
        """BUG REAL: la disponibilidad se publicaba "online" retenida UNA vez, al
        anunciar la entidad, y no se revocaba nunca. Con el dispositivo apagado o
        fuera de la LAN, HA seguia mostrandolo disponible con su ultimo estado
        retenido, aunque `manager.connected` ya fuera False. Climate ya lo hacia
        bien; los cuatro puentes no."""
        online = self._manager.connected(self.device_id)
        self._mqtt.publish(f"{base}/availability", "online" if online else "offline", retain=True)

    def _publish_light_state(self, index: int, lt) -> None:
        base = self._base(f"light{index}").format(domain="light")
        self._publish_availability(base)
        switch_val = self._manager.get_decoded(self.device_id, lt.switch_dp)
        self._mqtt.publish(f"{base}/state", "ON" if switch_val else "OFF", retain=True)
        if lt.brightness_dp is not None:
            b = self._manager.get_decoded(self.device_id, lt.brightness_dp)
            if b is not None:
                self._mqtt.publish(f"{base}/brightness/state", int(b), retain=True)

        # BUG FIXED HERE: se publicaban `color_temp/state` Y `hs/state` a
        # la vez, sin mirar el `work_mode_dp` real del dispositivo -- el
        # esquema MQTT "legacy" de HA infiere el `color_mode` activo de
        # CUAL topic recibio valor mas recientemente, no del dispositivo,
        # asi que publicar los dos siempre dejaba a HA adivinando (visto
        # en produccion: color_mode="hs" con un color que no era el
        # pedido, mientras el DP real `work_mode` seguia en "white").
        # Ahora solo se publica el topic del modo REALMENTE activo.
        work_mode = self._manager.get_decoded(self.device_id, lt.work_mode_dp) if lt.work_mode_dp is not None else None
        publish_color_temp = lt.color_temp_dp is not None and (work_mode is None or work_mode == lt.work_mode_white)
        publish_hs = lt.color_dp is not None and (work_mode is None or work_mode == lt.work_mode_colour)

        if publish_color_temp:
            ct = self._manager.get_decoded(self.device_id, lt.color_temp_dp)
            if ct is not None:
                # BUG FIXED HERE: se publicaba el valor CRUDO del DP
                # (escala 0..color_temp_max del fabricante) como si ya
                # fueran mireds -- ver el aviso en `_publish_light`.
                self._mqtt.publish(f"{base}/color_temp/state", light_dp_to_mireds(ct, lt), retain=True)
        if publish_hs:
            raw = self._manager.get_decoded(self.device_id, lt.color_dp)
            hs = decode_color_hs(lt, raw)
            if hs is not None:
                self._mqtt.publish(f"{base}/hs/state", f"{hs[0]:.1f},{hs[1]:.1f}", retain=True)

    def _publish_climate_state(self, index: int, cm) -> None:
        base = self._base(f"climate{index}").format(domain="climate")
        self._publish_availability(base)
        handle = self._manager.climate_handle(self.device_id, index)
        if handle is None:
            return
        self._mqtt.publish(f"{base}/mode/state", handle.hvac_mode, retain=True)
        if handle.current_temperature is not None:
            self._mqtt.publish(f"{base}/current_temp/state", handle.current_temperature, retain=True)
        if handle.target_temperature is not None:
            self._mqtt.publish(f"{base}/temp/state", handle.target_temperature, retain=True)

    @staticmethod
    def _encode_state(domain: str, value) -> str:
        if domain in ("switch", "binary_sensor"):
            return "ON" if value else "OFF"
        return "" if value is None else str(value)

    # ----------------------------------------------------------- comandos -
    # paho-mqtt normalmente ya protege su propio bucle de despacho contra
    # una excepcion de un callback, pero sin captura aqui un comando
    # llegado para un dispositivo momentaneamente desconectado (LAN caida,
    # reconectando...) se perderia sin dejar ni una linea de log --
    # exactamente el tipo de fallo silencioso contra el que ya se protege
    # el resto de este proyecto (ver coordinator.py original).

    # Todos los comandos se ejecutan en el worker, NO en el hilo de red de paho
    # -- ver ha_mqtt.MqttCommandWorker. Aqui era lo mas grave de todo el add-on:
    # `manager.set_dp` acaba en `_run_coro` + `future.result(timeout=10)`, asi
    # que un dispositivo Tuya que no respondiera bloqueaba hasta 10 SEGUNDOS el
    # hilo de red de paho, que es UNO para todo el add-on -- durante ese rato
    # ninguna entidad MQTT de ningun plugin respondia. Ademas, ahora se publica
    # el estado en cuanto el comando se aplica, en vez de esperar al siguiente
    # sondeo del dispositivo.
    #
    # El payload se decodifica/valida en el hilo de paho (solo parsear, cuesta
    # nada) y solo se encola si es correcto; el trabajo pesado va al worker.

    def _on_bool_command(self, dp, client, userdata, msg) -> None:
        on = msg.payload.decode(errors="replace").strip() == "ON"
        self._commands.submit(lambda: self._manager.set_dp(self.device_id, dp.dp_id, on))

    def _on_number_command(self, dp, client, userdata, msg) -> None:
        try:
            raw = dp.encode(float(msg.payload.decode(errors="replace")))
        except (TypeError, ValueError):
            log.warning("Tuya %s: payload numerico invalido para DP %s: %r", self.device_id, dp.dp_id, msg.payload)
            return
        self._commands.submit(lambda: self._manager.set_dp(self.device_id, dp.dp_id, raw))

    def _on_select_command(self, dp, client, userdata, msg) -> None:
        try:
            raw = dp.encode(msg.payload.decode(errors="replace"))
        except (TypeError, ValueError):
            log.warning("Tuya %s: payload de seleccion invalido para DP %s: %r", self.device_id, dp.dp_id, msg.payload)
            return
        self._commands.submit(lambda: self._manager.set_dp(self.device_id, dp.dp_id, raw))

    def _on_climate_mode(self, index, client, userdata, msg) -> None:
        mode = msg.payload.decode(errors="replace").strip()

        def apply() -> None:
            # El handle se resuelve DENTRO del worker: entre encolar y aplicar
            # el dispositivo puede haberse reconectado o cambiado de perfil.
            handle = self._manager.climate_handle(self.device_id, index)
            if handle:
                handle.set_hvac_mode(mode)

        self._commands.submit(apply)

    def _on_climate_temp(self, index, client, userdata, msg) -> None:
        try:
            value = float(msg.payload.decode(errors="replace"))
        except ValueError:
            log.warning("Tuya %s: temperatura climate invalida: %r", self.device_id, msg.payload)
            return

        def apply() -> None:
            handle = self._manager.climate_handle(self.device_id, index)
            if handle:
                handle.set_temperature(value)

        self._commands.submit(apply)

    def _light(self, index: int):
        profile = self._manager.profile(self.device_id)
        return profile.lights[index] if profile and index < len(profile.lights) else None

    def _on_light_power(self, index, client, userdata, msg) -> None:
        on = msg.payload.decode(errors="replace").strip() == "ON"

        def apply() -> None:
            lt = self._light(index)
            if lt is not None:
                self._manager.set_dp(self.device_id, lt.switch_dp, on)

        self._commands.submit(apply)

    def _on_light_brightness(self, index, client, userdata, msg) -> None:
        try:
            requested = float(msg.payload.decode(errors="replace"))
        except ValueError:
            log.warning("Tuya %s: brillo invalido para luz %s: %r", self.device_id, index, msg.payload)
            return

        def apply() -> None:
            lt = self._light(index)
            if lt is None or lt.brightness_dp is None:
                return
            val = max(int(lt.brightness_min), min(int(lt.brightness_max), round(requested)))
            if lt.work_mode_dp is not None:
                # Poner en modo "blanco" ANTES del brillo -- si el
                # dispositivo esta en modo color, cambiar el brillo del
                # lado blanco no tendria efecto visible hasta que se
                # cambia de modo de todos modos.
                self._manager.set_dp(self.device_id, lt.work_mode_dp, lt.work_mode_white)
            self._manager.set_dp(self.device_id, lt.brightness_dp, val)

        self._commands.submit(apply)

    def _on_light_color_temp(self, index, client, userdata, msg) -> None:
        try:
            requested = float(msg.payload.decode(errors="replace"))
        except ValueError:
            log.warning("Tuya %s: temperatura de color invalida para luz %s: %r", self.device_id, index, msg.payload)
            return

        def apply() -> None:
            lt = self._light(index)
            if lt is None or lt.color_temp_dp is None:
                return
            # BUG FIXED HERE: esto clampaba el valor RECIBIDO (mireds de
            # verdad, ver `_publish_light`) directo al rango del DP
            # (0..color_temp_max, escala del fabricante) sin convertir --
            # ver el aviso de arriba y `tuya/profile.py:mireds_to_light_dp`.
            mireds = max(LIGHT_MIN_MIREDS, min(LIGHT_MAX_MIREDS, round(requested)))
            val = mireds_to_light_dp(mireds, lt)
            if lt.work_mode_dp is not None:
                self._manager.set_dp(self.device_id, lt.work_mode_dp, lt.work_mode_white)
            self._manager.set_dp(self.device_id, lt.color_temp_dp, val)

        self._commands.submit(apply)

    def _on_light_hs(self, index, client, userdata, msg) -> None:
        try:
            h_str, s_str = msg.payload.decode(errors="replace").split(",")
            h, s = float(h_str), float(s_str)
        except ValueError:
            log.warning("Tuya %s: color invalido para luz %s: %r", self.device_id, index, msg.payload)
            return

        def apply() -> None:
            lt = self._light(index)
            if lt is None or lt.color_dp is None:
                return
            raw = encode_color_hs(lt, h, s)
            if lt.work_mode_dp is not None:
                self._manager.set_dp(self.device_id, lt.work_mode_dp, lt.work_mode_colour)
            self._manager.set_dp(self.device_id, lt.color_dp, raw)

        self._commands.submit(apply)
