# Nivel 2 — Motor de ejecución del panel headless: cadena de ARRANQUE (2026-09-30)

Objetivo: arrancar el panel Ray de CUALQUIER producto headless en el runtime service
(donde reconcilia React), montar la página y capturar en `ty.device.sendMqttMessage`/
`publishDps` lo que la app enviaría — auto-derivando capacidades/UI/comandos sin nada a mano.
(Nivel 2 subsume al Nivel 1: si el panel se ejecuta entero, declara y renderiza todo.)

## Arquitectura confirmada
- El reconciler React está en `main.js` (SERVICE): `Cpe(element,container)` = render
  (`ox.createContainer`/`updateContainer`); `createElement`×1031, `useState`×134.
  `view.js` es solo la UI nativa. => montar en el runtime service es viable.
- `entryPagePath: /pages/home/index`, todas las páginas `render:"ray"`.

## Cadena de arranque (mapeada leyendo service.js)
1. `window.ServiceJSBridge` (real) expone `trigger(evento,data,cb)` = entrada native→JS.
2. Boot lee las LAUNCH OPTIONS del runtime WEB desde **`location.search`**:
   `getLaunchOptionsSync(){var e=location.search;var t=wu(e);return {path:t.path,query:wu(t.query),scene:10001}}`.
   => set JSDOM url = `https://localhost/?path=<enc /pages/home/index>&query=<enc "deviceId=..&devId=..">`.
3. `It(opts,cb)` sondea `ac.getLaunchOptions` (via native→uf()/location) 15×100ms; con `path`
   -> `cb(opts)` -> `Ue.launch(opts)` (Ue = manager, privado).
4. `Ue.launch({path,query},webviewId)` -> `createPage({webviewId,url})` -> `pushPageInst` ->
   `pageInst.init()` -> `Cpe(v7.createElement(pageComponent,{page,query,...}),container)` -> React.

## Estado actual (tools/panel_boot.js)
- Carga service.js + 40 scripts + main.js OK. `ServiceJSBridge` operativo.
- Con la url de lanzamiento en `location.search`, el boot COGE las launch options y ENTRA en
  `Ue.launch` -> `createPage` (¡la maquinaria de arranque se ejecuta headless!). `getDeviceInfo`
  se llama durante el arranque.
- BLOQUEANTE ACTUAL: crash en `launch`: `s=(n||xr()).toString()` — `xr()` (webviewId de la
  página actual) devuelve `undefined` en la 1ª página headless (normalmente lo asigna el nativo).
  => siguiente: proveer el webviewId (fuente de `xr()`), p.ej. por param en location o global.

## Siguiente (F4-boot)
1. Resolver `xr()` (webviewId) para la 1ª página -> `createPage` completa.
2. Cablear/stubear el HOST CONFIG del reconciler (createInstance/appendChild -> ops a view):
   que la reconciliación termine headless sin nativo (capturar/no-op las ops de vista).
3. Página montada -> recorrer el árbol/fiber para AUTO-derivar capacidades+UI, e invocar
   handlers (p.ej. room clean) para capturar el envelope en sendMqttMessage/publishDps.
4. Generalizar a cualquier producto (cambiar panel+device) -> descriptor completo automático.

Regla: todo LOCAL, nunca producción; no enviar comandos derivados a hardware sin verificación.

## Avance 2026-09-30 (tarde): página CREADA + applyRender ejecutándose
Progreso mayor (tools/panel_boot.js expone win; tools/panel_mount.js monta):
- **`win.ROUTER`** (manager, expuesto global) es reachable. `ROUTER.launch({path:"/pages/home/index",
  query:"deviceId=..&devId=.."}, "1")` (webviewId explícito "1") **CREA la página**:
  `getCurrentPages() == 1`, dispara `page_create` con el deviceId, y el panel llama a `getDeviceInfo`.
- **Patch clave del handshake de vista**: el render (applyRender->Cpe) está gated tras el
  round-trip service<->view (los `publish` esperan ack de la vista, que headless no llega).
  Como el código interno usa `k.publish` y `k===window.ServiceJSBridge` (misma referencia),
  se PARCHEA `win.ServiceJSBridge.publish` para invocar el callback al instante (ack falso).
  Con eso: `publishCount=7` y **se dispara `page_first_render`** => `applyRender` YA se ejecuta.
- Lifecycle: valores string ("onLoad"/"onShow"/"onReady"). El manager es `win.ROUTER`. El
  PageInst de servicio (con applyRender) vive en el registro privado `Vi` (no reachable directo).

### Gap actual (siguiente): el árbol React no monta
Aunque applyRender corre, la reconciliación React no produce árbol montado
(`getDeviceInfo`=1, sin `getDp`/`subscribeDeviceRepDps`/`publishDps`; sin `_rootContainer`).
La página Ray (main.js ~318614) monta React EN SU CONSTRUCTOR:
`o=v7.createElement(compRaiz,{page,query,...}); a._mount ? (this.element=O7(o,container,pageId), a._mount(this)) : this.element=Cpe(o,container)`.
Hipótesis a resolver:
1. La página Ray (con Cpe/O7) no se está construyendo (getCurrentPages()[0] es el wrapper
   del framework, no la página Ray con Cpe) -> falta el paso que instancia la página Ray.
2. Toma la rama `O7`+`a._mount` (no Cpe) -> el marcador `_rootContainer` no aplica; verificar O7.
3. La reconciliación React ocurre en el runtime VIEW (view.js) y hay que puentear
   service<->view con AMBOS contextos (view reconcilia usando el bundle de vista).
Siguiente: instrumentar si el constructor de la página Ray corre / si es Cpe vs O7 / o montar
el runtime view y puentear. (render_host.js ya arranca view.js.)
