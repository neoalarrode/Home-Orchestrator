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
