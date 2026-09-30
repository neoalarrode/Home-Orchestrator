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

## Estado fino y ÚNICO blocker para render limpio (2026-09-30)
El pipeline de render funciona y captura el VDOM completo. PERO la home se renderiza como el
**error-boundary de la app** ("发生点意外 / 提交" = algo inesperado / enviar feedback), no la UI real.
Causa raíz identificada: el **auto-boot del framework** (`It` lee launch options de location.search
y llama `Ue.launch(t)` con UN arg, sin webviewId) crashea en `(n||xr()).toString()` porque en modo
WEB la primera página no tiene webviewId: `fg` (current webview) SOLO lo setea `Dr(id)` (dentro de
`launch`), sin default de arranque. El error async sube y el error-boundary de React reemplaza el árbol.
- `xr()`/`Ot()` devuelven `fg` (=undefined al inicio). `Dr(e){fg=e}` (service.js ~418293). Sin Dr de boot.
- Mi `ROUTER.launch({path},"1")` SÍ pasa webviewId y llama Dr("1"), pero el auto-boot `It` dispara ANTES
  (poll rápido en cuanto location.search tiene path) y crashea primero.
SOLUCIONES A PROBAR (próxima sesión, es 1 detalle de handshake):
1. Presetear el webviewId de entrada como hace el host web real (buscar si el host web setea `fg`
   via un mensaje del view o un global/param antes de `It`; o si el view frame, al cargar, postea su id).
2. Ganarle la carrera a `It`: en run_bridge2 (addInitScript, corre pronto en el frame service),
   micro-poll de `window.ROUTER` y llamar `ROUTER.launch({path},"1")` ANTES de que `It` resuelva su poll
   (así `Dr("1")` setea `fg` y el auto-boot de `It` ya no crashea).
3. Suprimir el auto-boot (quitar `path` de location.search) y arreglar el `indexOf`(e.url undefined) que
   aparece entonces en MI launch (investigar por qué r.path no llega; posible redirect a functional-page).
Con el render limpio: parsear el árbol (helper ya prototipado: nodos con PS["bind:tap"]="eh"+data-sid,
paths de vnode) -> controles reales (room clean, etc.) -> postear domEvent {eventName:"domEvent",
options:{eventList:[{ev,paths,eventName:"tap"}]}} -> capturar el envelope del comando.


## Refinado (2026-09-30, cont.): quitar path del launch elimina el crash del auto-boot
- Con `path` en location.search: el auto-boot `It` crashea (webviewId) -> error-boundary.
- **Sin `path` (solo `query=deviceId`) + early-launch explícito desde el frame service**
  (run_bridge2: micro-poll de ROUTER -> `ROUTER.launch({path,query},"1")`): el crash del
  auto-boot DESAPARECE (ya no sale el error-boundary). Pero aparece un `indexOf`(e.url undefined)
  en OTRA llamada de launch (171020) -> probable navegación interna de la home a una
  functional-page/sub-ruta con url vacía; el render queda en esqueleto (17 ops, sin textos/taps).
- SIGUIENTE (sesión enfocada): traza quién llama a launch con url undefined (¿la home navega a
  roomFloor/functional al montar?) y proveer esa ruta/url; o interceptar esa navegación. Con eso
  la home real monta -> controles (room clean) -> domEvent tap -> captura del envelope.
- `earlyLaunch:true` confirmado (mi launch corre y setea fg), pero no gana la carrera a `It`
  cuando hay path; por eso la vía correcta es quitar path y lanzar explícito.


## ✅ RESUELTO (2026-10-01): la HOME REAL renderiza headless — opción 2 completa
Fix del blocker: `xr()` (webviewId) = `window.__webviewId__` || parse(location.search).webviewId
(service.js ~314087), NO `fg`. Basta con `webviewId=1` en location.search (+ window.__webviewId__="1").
Con eso el auto-boot del framework lanza LIMPIO (sin toString/indexOf) y la home real monta.
RESULTADO: 17 ops setData, **80 textos** de la UI real del aspirador (房间编辑/room edit, 禁区编辑/
restricted, 开始/start, Smart/Room/Zone/Pose, 集尘/洗拖布/烘干, 清洁偏好/清扫模式, área m²/min/%...),
**59 handlers de tap** (bind:tap + data-sid). = MODELO DE CAPACIDADES+UI COMPLETO auto-derivado de la app.
Setup final: location.search = ?path=/pages/home/index&query=deviceId=..&webviewId=1 ; window.__webviewId__="1".
SIGUIENTE: mergear los ops setData -> árbol completo -> path de vnode de un control (ej. 开始/房间编辑)
-> postear domEvent tap -> capturar el envelope del comando (roomCleanSet, etc.).


## Estado 2026-10-01 (cont.): home renderiza + 59 controles; queda limpiar 1 error residual
La home REAL renderiza headless y se capturan 80 textos + **59 handlers de tap** (room edit,
start, Smart/Room/Zone/Pose, base station, clean preference...) = capability/UI model completo.
ADEMÁS se renderiza un subárbol error-boundary por un error residual: un STORAGE con ámbito
(clase IV) lanza "id is required" en module-init -> luego `this.storage.get` sobre undefined
(main.js:1153279) -> rayjs-error-catch. La location.search del iframe es correcta (lleva deviceId),
así que es un edge de orden-de-carga/id (el singleton de storage se construye y `Dg().query.deviceId`
no resuelve en ese instante). NO impide el render de la home (ambos árboles están en el stream).
Herramienta build_tree.js: mergea los ops setData (patchData con claves dotted "root.NS.x") y navega
node.NS[node.CH[i]] para hallar nodos bind:tap con su path. Hoy el merge queda con el error-boundary
(último patch en root); para el tap hay que (a) limpiar el error del storage (para árbol limpio) o
(b) extraer los controles de la home de los ops crudos (59 detectados) con su path dotted->indices.
SIGUIENTE: resolver el id del storage (probar uid/groupId, o precargar Dg) -> árbol limpio ->
path del control -> domEvent tap -> capturar envelope del comando.


## 2026-10-01 (final de la tanda): MODELO DE UI EXTRAÍDO + tap round-trip CONFIRMADO
- Fix storage: groupId="" mantiene device single; el error residual del storage NO impide el render
  de la home (op jobId=2 trae el árbol completo). `home_tree.js` coge el op con más textos, mergea su
  patchData (claves dotted) y navega node.NS[node.CH[i]] -> **28 controles con path + etiqueta**:
  开始/Start [0,0,4,0,3,0,0], 房间编辑/room-edit [0,0,3,0,0,1,1], 禁区编辑, Smart/Room/Zone/Pose
  [0,0,4,0,1,0..3], 集尘/洗拖布/烘干, 清洁偏好, 全局/自定义模式, 地板材质, 重置密码, 好的/跳过...
  -> docs/conga_home_controls.json. ES EL MODELO CAPACIDADES+UI auto-derivado (objetivo del usuario).
- TAP round-trip CONFIRMADO: postear domEvent {eventName:"domEvent",options:{eventList:[{ev,paths,
  eventName:"tap"}]}} con el path del control INVOCA su handler (se ve el flujo del handler ejecutarse).
- Pendiente para capturar el envelope de un comando concreto (ej roomCleanSet): 开始/Start dispara un
  flujo complejo (获取3D家具列表/lista de muebles 3D, mapa...) que necesita más andamiaje de kits/estado
  antes de emitir el comando ("s is not a function" en el .catch del fetch de muebles). Opciones:
  (a) satisfacer las deps del flujo Start (MapKit/SweeperKit + datos), o (b) tapear un control de comando
  directo (p.ej. base-station 集尘) tras abrir su popup, o (c) usar el formato ya derivado por fuente
  (panel_commands.js, roomCleanSet verificado) + la captura viva de queries (passwordQry proto64) como
  verificación. Para el PLUGIN, los formatos de comando ya se conocen (descriptor + transporte verificado).
