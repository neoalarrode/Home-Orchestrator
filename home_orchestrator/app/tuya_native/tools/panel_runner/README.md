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

## Arquitectura de integración (2026-09-30) — solución dinámica completa
El objetivo del usuario es CONTROLAR desde HA (headless), no ver UI. Para eso el plugin
necesita por dispositivo: (a) capacidades/parametros, (b) como encodear cada comando.

Fuentes dinámicas, todas derivadas de la app, cero a mano:
1. **Capacidades (a)**: el THING-MODEL (profiles.parse_thing_model) ya da TODOS los DPs con
   code/type/rango/unidad/rw. Control estandar = publishDps({dps:{code:valor}}) — cubre la
   mayoria (riego, AC, luces, enchufes, covers, fans, sensores), incluso de panel custom.
2. **Comandos encodeados (b)**: 
   - DESCRIPTOR por fuente (tools/panel_commands.js): parsea el dispatch del panel/main.js y
     emite {reqType, version, protocol, message_fields} por comando. Es la logica de la app.
   - VERIFICACION/derivacion en vivo (este panel-runner): ejecuta el panel real headless y
     CAPTURA los envelopes que emite (probado: passwordQry proto64 v1.0.0 == builder Ai).
     Confirma que el descriptor casa con la app EXACTAMENTE.

Limite actual del runner: solo corre el runtime SERVICE (no el frame de vista). Captura lo
que el panel emite por si mismo (queries, y comandos auto). Los comandos de ACCION de usuario
(ej. room clean con habitaciones) no se pueden DISPARAR headless sin montar el frame de vista
(DOM+tap) o alcanzar el store (privado, no reachable desde window). Para esos, el formato ya
lo da el descriptor por fuente (verificado 1:1 con lo que la app emite en los que si capturamos).

PROXIMO opcional (si se quiere disparar cualquier comando y leer el arbol de UI completo):
reconstruir el launcher web service<->view (dos contextos + puente postMessage) en Chromium,
para que el panel renderice a DOM real; entonces se leen controles y se simulan taps.

## OPCIÓN 2 LOGRADA (2026-09-30): runtime service+view -> ÁRBOL DE UI + comandos
Topología real del runtime WEB de Ray: service y view son IFRAMES que hablan por
`window.parent.postMessage` (bridge `mm`; `ue()`=native elige worker `Em`, web elige `mm`).
Montaje headless (tools/panel_runner/{panel_runner2.js, run_bridge2.js, parent.html}):
- parent.html = página normal (no Ray, así `page.evaluate` funciona) con un iframe al bundle
  service (service.html) + RELAY: captura todo iframe->parent en `window.__STREAM` y ACKea los
  `$syncCallbackKey` (hace de "vista" -> desbloquea el pipeline de render del service).
- run_bridge2.js (addInitScript, todos los frames): getNativeKits()->DeviceKit (device+captura)
  + ty shim. panel_runner2 arranca el iframe, hace `iframe.ROUTER.launch({path,query},"1")`, y
  el parent postea `{$eventName:"window.onViewLoad", pageId:"1"}` al iframe -> el service RENDERIZA.
RESULTADO: __STREAM con 37 msgs = el stream de render COMPLETO: devInfo, toasts de estado real
("请装回尘盒再启动"=pon la caja de polvo), componentsConfigMap, PAGE_FIRST_RENDER, y **16 ops
`action:"setData"` con `patchData` = el VDOM real** (186 view, text, image, button, input, icons,
popups, nav-bar...). Eso es el MODELO DE CAPACIDADES+UI auto-derivado de la app, sin nada a mano.

### Disparar comandos (view->service), mecanismo mapeado
Nodos interactivos: `PS:{"bind:tap":"eh","data-sid":N}` + `TX` (texto). El service escucha
`$eventName:"domEvent"` con `{pageId, options:{eventList:[{ev:{currentTarget,target}, paths:<vnode
leaf path>, eventName:"tap"}]}}` -> `searchVnodeByLeafPath(paths)` -> invoca el handler -> el
comando sale por DeviceKit.sendMqttMessage/publishDps (capturado). Falta: calcular el `paths` del
nodo objetivo desde el árbol y postearlo -> captura el envelope del comando (ej. roomCleanSet).
