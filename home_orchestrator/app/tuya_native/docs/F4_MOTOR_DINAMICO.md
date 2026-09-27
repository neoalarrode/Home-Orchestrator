# F4 — Motor dinámico "Todo lo gordo": estado y arquitectura (2026-09-27)

Objetivo: ejecutar el panel de CADA producto headless y CAPTURAR automáticamente el
comando que la app enviaría (sin portar encoders a mano), para cualquier dispositivo.

## Arquitectura descubierta (leyendo el original)
El panel Ray/Godzilla tiene DOS runtimes, como en la app:
- **Service runtime** (`fw/service.js` + `main.js`): lógica y ACCIONES. Las acciones
  (`setRoomClean`, `setZoneClean`, `setVirtualArea/Wall`, `setSpotClean`...) son
  **hooks React**: `Ej=t=>{const{useMqtt:r,commandVersion:i,devices:o}=useContext(dc);
  return {...}}`. Eligen transporte por `useMqtt`: proto64 `ty.device.sendMqttMessage`
  (envelope `Ai`: protocol 64, version "1.0.0", taskId, {reqType, ...campos}) o DP
  `command_trans.set`→`ty.device.publishDps` (legacy). Los ENCODERS y `Ej` son
  **privados del closure del módulo main.js** (no exportados globalmente).
- **Render runtime** (`fw/view.js` + chunks `*.rjs.js`/`*.tpl.js`): componentes y los
  encoders puros de bytes (`encodeRoomClean0x14`, `encodeVirtualWall0x12`, ...). Se
  registran vía `defineRenderScript`/`requireRenderScript` (`window.__renderScriptModules__`).

## Arneses (persistentes, en tools/)
- `tools/panel_host.js` (host5): jsdom + service.js + 40 scripts + main.js → 0 errores.
  DeviceKit inyectado: `getDeviceInfo`=dispositivo real; `publishDps`/`sendMqttMessage`/
  `publishMessage` CAPTURADOS (transporte out). Requiere ./fw ./panel ./device.json.
- `tools/render_host.js` (hostview): jsdom + STUBS (Worker/MessageChannel/importScripts/
  requestIdleCallback) + view.js → `defineRenderScript`/`requireRenderScript` OK; carga los
  38 chunks render; el chunk de encoders `c-a6f984cf.rjs.js` y su dep `chunk-OCYIROLK.rjs.js`
  REGISTRAN. `requireRenderScript(ENC)` aún falla.

## Confirmado (F3, ya en producción y verificado en vivo)
Room clean del Conga = `sendMqttMessage({protocol:64, message:{reqType:"roomCleanSet",
version:"1.0.0", taskId, ids,suctions,cisterns,cleanCounts,yMops,sweepMopModes,num}})`.
Coincide 1:1 con `device_manager._room_clean`. Ver PANEL_DISPATCH_ROOMCLEAN.md.

## Bloqueantes restantes para el AUTO-captura genérico (cada uno resoluble)
1. **Render**: `requireRenderScript("c-a6f984cf.rjs.js")` ejecuta el factory al requerir,
   que hace `X.createElement(...)` donde `X` = otro módulo render (React-core) que el chunk
   importa vía `requireRenderScript(<id>)` y que NO está registrado → resuelve a `undefined`.
   Un `window.React`/`createElement` global NO basta (la llamada es sobre la referencia
   importada, no global). Falta localizar y registrar el módulo render-React core (probable
   en view.js o un chunk aparte) para que el import del encoder resuelva. Alternativa: aislar
   los encoders puros (están mezclados con componentes en el mismo .rjs) o cargar el módulo
   React-core primero en el registro `__renderScriptModules__`.
2. **Orden de carga** de chunks `.tpl.js`: los `chunk-*.tpl.js` deben cargarse ANTES que
   sus dependientes (`c-*.tpl.js`) — ordenar por dependencia o cargar dos pasadas.
3. **Service**: para invocar las acciones (`Ej`) headless y capturar el envelope por
   `sendMqttMessage`, hace falta un shim mínimo de render React que alimente el contexto
   `dc` = {useMqtt, commandVersion, devices→store con .common.model.actions}. Como `Ej`
   es privado del closure, se invoca renderizando el componente de página que lo usa
   (`Ej(device).setRoomClean` en un handler), o interceptando en el límite `ty.device.*`.

## Recomendación
F3 entregado y en prod. F4 (motor genérico) es endurecimiento multi-paso de ambos
runtimes (shims React de render + service + orden de chunks). Abordar como fase dedicada.
