# Panel-runner headless (Chromium) — motor "Nivel 2" INTERNO

Ejecuta el panel Ray real de un producto en Chromium headless (playwright-core) y CAPTURA
lo que la app enviaría por `ty.device.sendMqttMessage`/`publishDps` — el transporte EXACTO,
sin portar nada a mano y sin mostrar UI al usuario. Salida = descriptor por producto que el
plugin usa para controlar desde HA/MQTT (headless). Paso de derivación interno + cacheable.

## Estado (2026-09-30): PROBADO end-to-end
Con el panel del Conga cargado, el runner arranca el runtime service, monta componentes
(getDeviceInfo, dpCbs registrados) y **captura el envelope MQTT real** que el panel emite en
el montaje:
`{"deviceId":"…","message":{"reqType":"passwordQry","version":"1.0.0","taskId":"…"},"options":{},"protocol":64}`
— coincide EXACTO con el builder `Ai` de la app (reqType/version 1.0.0/protocol 64/taskId).

## Cómo funciona
- Sirve `web/` (panel + `framework/` = fw del framework Ray) por http.
- `run_bridge.js` (inyectado con addInitScript ANTES de los scripts del panel): implementa el
  puente nativo — `getNativeKits()` → DeviceKit (getDeviceInfo con schema del thing-model +
  dps; publishDps/sendMqttMessage/registerMQTTProtocolListener… CAPTURADOS), `ty` shim
  (getSystemInfoSync/getLaunchOptions(Sync)…). Lanza vía `window.ROUTER.launch({path,query},"1")`
  y parchea `ServiceJSBridge.publish` con ack inmediato (desbloquea el handshake de vista).
- `panel_runner.js`: levanta el server + Chromium (Google Chrome for Testing del cache de
  playwright), carga host.html, ejecuta y lee `window.__CAPTURED` por console.

## Entradas necesarias por producto
- El panel extraído (panel_store.fetch_panel) + el framework Ray (panel_store.fetch_framework).
- El thing-model del dispositivo (profiles.parse_thing_model) → se construye `schema` (array
  {id,dpId,code,mode,type,property}) que la app reduce en `__dpSchema__`.
- deviceId + dps (localKey NO va al repo; queda local).

## Siguiente
- Disparar acciones concretas (ej. room clean) para capturar `roomCleanSet` con parámetros
  (via store/redux o montando el frame de vista y simulando el tap).
- Recorrer el árbol de render (setData) para el modelo de capacidades+UI completo.
- Integrar como paso de derivación del plugin (cachea descriptor por productId).
