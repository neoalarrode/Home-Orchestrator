# Proyecto "Todo lo gordo" — Motor headless del panel Tuya (control 100% fiel a la app)

## Objetivo
Que el plugin Tuya de Home Orchestrator controle CUALQUIER dispositivo Tuya
(actual o futuro) **exactamente como la app oficial**, de forma **dinámica** y
**sin portar ningún encoder a mano**. La app no tiene los encoders escritos: los
EJECUTA. El panel (mini-programa Godzilla/Ray) de cada producto trae TODOS los
encoders/decoders y la lógica de "cómo comunicarse" con ese equipo. Por tanto el
plugin debe **descargar y EJECUTAR el JS del panel** de cada producto, e
interceptar sus llamadas de transporte para enviarlas con nuestra capa de red ya
verificada. Cero encoders portados => no puede "faltar" ninguno.

## Principio rector
- El panel CALCULA el payload y ELIGE el transporte (publishDps / sendMqttMessage
  proto64 / command_trans DP). Nosotros proveemos el I/O real (MQTT app-channel con
  localKey de la APP, o HTTP publishDps). El JS nunca hace red por su cuenta: todo
  pasa por nuestros stubs.
- Capacidades = lo que el panel declara/gatea por thing-model (isSupportDp), no un
  mapa fijo por categoría.

## Cimientos YA construidos y verificados (reutilizar, NO rehacer)
- Firma app-fiel (clave K, HMAC-SHA256), login email/password + QR, re-auth. [client/auth/hmac_signer/signing/encryption/login_crypto]
- thing-model dinámico por dispositivo. [profiles/controller]
- Canal MQTT app->robot proto64 (pv2.3, GCM) + credenciales. VERIFICADO (roomCleanSet limpió el Salón). [mqtt_transport]
- **localKey de la APP** por dispositivo (thing.m.my.group.device.list, gid como query param). CLAVE: el MQTT cifra con esta, no la del IoT. [tuya_native_plugin._fetch_app_local_keys]
- HTTP publishDps (comandos DP; actúa de verdad). [controller.set_dp prefer=http]
- Descarga del mapa cloud: storage.config.get + file.list(history/*.bin) + presign SigV4 + JSON. [cloud_storage/sweeper_map/map_decoder]
- device list con atributos (category, devAttribute bitmask, skills, pv). 
- Descarga del panel (probado a mano): ui.info.batch.get -> bizClientId(=miniprogramId) -> miniprogram.info.get v4.0 -> codeDownloadUrl -> tar.gz EN CLARO. Panel del Conga extraído en ~/.tuya_native/probe/panel_pkg.bin.
- Puente device_registry (consumo interno Climate/Lighting), entidades HA nativas por MQTT Discovery, expose_mqtt.

## Fases

### F1 — Descarga y caché del panel por producto
- Reproducir en código la cadena ui.info -> miniprogramId (bizClientId) -> miniprogram.info.get v4.0 -> codeDownloadUrl -> descargar tar.gz -> extraer.
  - PENDIENTE: fijar params exactos de miniprogram.info.get v4.0 (el intento dio BUSI_PROGRAM_NOT_EXIST / PARAM_ALL_INPUT_LOSS; usar miniprogramId=bizClientId "tyskl...", jssdkVersion/bundleId/paramsJson correctos — ver GZLAtopRequest.java).
- Caché en /data/tuya_panels/<productId>/<version>/ con verificación de integridad (sign/md5). Reuso entre dispositivos del mismo productId.
- Entregable: `panel_store.py` (fetch+cache+extract) -> devuelve rutas de app-service.json/app-config.json/main.js/chunks.

### F2 — Motor JS embebido (headless)
- Elegir engine ejecutable en el add-on (arm64, sin toolchain pesada): candidatos QuickJS (binding python `quickjs`/`quickjs-ng`) o Node embebido. Criterio: arranca los scripts de app-service.json.
- Cargar el runtime del panel: framework/service.js + los `scripts` que lista app-service.json (chunks) en el orden dado.
- RIESGO PRINCIPAL (de-riesgar DENTRO de esta fase): el bundle mezcla UI (Ray/React) + lógica. Hay que poder ejecutar la CAPA DE SERVICIO/lógica de control sin render de UI. Plan:
  1. Cargar solo el "service"/worker global (app-service.json.scripts, workersGlobal) que es donde vive la lógica de dispositivo, no el webview de UI.
  2. Stubbear las APIs de UI (componentes Ray) a no-ops para que la carga no explote.
  3. Si el service no arranca aislado -> fallback documentado (ejecutar en Node con un shim más completo del runtime Ray).
- Entregable: `panel_engine.py` que carga un panel y expone `call_action(name, args)`.

### F3 — Puente/stubs del contenedor mini-app (lo que el panel espera)
Implementar en Python (expuestos al JS) las APIs del contenedor Tuya, mínimas para
la lógica de control:
- Modelo de dispositivo: getDeviceInfo, dpSchema/thing-model, dps actuales, isSupportDp, getProductId, devAttribute/skills/pv.
- Transporte (INTERCEPTADO): publishDps(dps), sendMqttMessage({protocol,message}), model.actions.<x>.set(v), command_trans. -> enrutar a:
  - publishDps/command_trans -> HTTP publish_dps (o LAN si algún día).
  - sendMqttMessage(proto64) -> mqtt_transport.build_frame con **localKey de la APP** -> smart/mb/out/<devId>.
- Infra para que no rompa: storage(get/set), i18n (cachear thing.m.miniprogram.i18n.get), logging, timers, fetch (bloqueado/whitelisted -> pasa por nuestro client firmado).
- Entregable: `panel_bridge.py`.

### F4 — Descubrimiento dinámico de capacidades y comandos
- Al cargar un dispositivo: cargar su panel, y ENUMERAR las acciones/reqTypes que expone y gatea por isSupportDp (roomCleanSet/Qry, zoneClean 0x3b, spotClean 0x16, virtualWall 0x12/0x13, virtualArea 0x1a/0x1b, useMap 0x2e/saveMap/deleteMap/resetMap, setRoomName/Order/Property, sceneList/trigger, lightKit escenas/gradientes, etc.).
- Construir un **perfil de capacidades por dispositivo** (dinámico) => qué entidades/servicios HA exponer y qué acción del panel invoca cada uno.
- Entregable: `capabilities.py` (perfil por device) sustituyendo el CATEGORY_MAIN + lógica hardcodeada.

### F5 — Puente a Home Assistant
- Mapear el perfil de capacidades a entidades/servicios HA nativos: vacuum (clean_area/segments, zonas via send_command, fan, mop), climate, light (kit), cover, fan, switch, sensores, y el mapa como entidad `image` (render aparte, opcional).
- Cada comando HA -> `panel_engine.call_action(...)` -> stubs -> red real.
- Reusar lo ya hecho (discovery, expose_mqtt, device_registry para consumo interno).

### F6 — Estado inverso (decoders del panel)
- Los mensajes del dispositivo (robot->app proto65, DP reports, mapa) se DECODIFICAN también con la lógica del panel (no a mano): suscribir smart/mb/in/<devId> + dp.get, pasar por el panel, y publicar estado/mapa/habitaciones a HA.
- Entregable: canal de estado bidireccional por el motor.

### F7 — Robustez y operación
- Caché de paneles por productId+version; carga perezosa por dispositivo; límites de memoria/CPU del engine; un engine por producto reutilizado entre dispositivos iguales.
- Aislamiento: el JS solo llega a la red por nuestros stubs (sin sockets propios).
- Re-auth/rotación de sesión y de localKey (ya dinámica). Degradación: si un panel no carga, caer al camino genérico (thing-model + DP HTTP) y avisar.

### F8 — Verificación y despliegue
- Probar contra TODOS los tipos reales del usuario (kt/qn climate, dj light, sd vacuum, cz/ggq/msp switch, cover/fan) — control y estado, zero-drift.
- Review independiente hasta ronda limpia (regla de no-prod-sin-prueba).
- Release durable: merge rama a main + tag + plugins.json (sha256), sustituyendo lo que hoy vive en /data/plugins/tuya/current.

## Orden de trabajo
F1 -> F2 (con de-riesgo del runtime dentro) -> F3 -> F4 -> F5 -> F6 -> F7 -> F8.
El motor (F2/F3) es el corazón y el mayor riesgo; si el runtime Ray no se puede
aislar headless, se evalúa Node con shim del framework antes de descartar.

## Estado actual del plugin (punto de partida)
Funciona hoy (camino genérico, con algo hardcodeado que "Todo lo gordo" sustituye):
comandos del aspirador por HTTP, room clean por MQTT+localKey app (ids enteros),
mapa/habitaciones desde cloud, entidades HA nativas, consumo interno Climate/Lighting,
login QR+password, expose_mqtt. Desplegado en /data/plugins/tuya/current (rama tuya-native-wip).
