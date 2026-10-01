# F4 "Todo lo gordo" — subfases del motor dinámico

Meta: ejecutar el panel de CADA producto headless y CAPTURAR automáticamente el comando
que la app enviaría (encoder + transporte), sin portar nada a mano. Base ya lista:
`tools/panel_host.js` (service, 0 err, captura transporte) y `tools/render_host.js`
(render arranca y registra módulos). Ver F4_MOTOR_DINAMICO.md / PANEL_DISPATCH_ROOMCLEAN.md.

## F4.1 — API nativa de render (desbloqueo del factory) [HECHO — con hallazgo]
Los factories de los módulos render reciben como args objetos nativos (`r.getCanvasById`,
`r.getSystemInfo`, `r.getBoundingClientRectById`, `createElement`, document...) que hoy
llegan `undefined` porque llamamos `requireRenderScript` fuera de un render. 
- Averiguar el ORDEN EXACTO de args que `view.js` pasa al factory (leer view.js).
- Construir esos objetos de verdad (respaldados por jsdom / stubs correctos), no proxies.
- RESUELTO el mecanismo: `view.js` llama al factory como
  `factory(require, module, exports, getAppApi(), Promise, ...requireParams)` (engine rjs,
  `requireArgsFactory=[()=>getAppApi(),()=>Promise]`; scope no-service → no se trunca).
  Implementado `tools/render_loader.js`: mini-require fiel (resuelve deps relativas contra
  `__renderScriptModules__`, runtime esbuild `chunk-OCYIROLK` real, `getAppApi` sustituido
  por un nativeApi + `env` window/document con deepStub → html2canvas init sobrevive).
- **HALLAZGO decisivo**: el módulo de encoders `c-a6f984cf.rjs.js` exporta SOLO
  `{default: <componente de mapa>}`. Los encoders (`encodeRoomClean0x14`, ...) son una
  dependencia commonJS ANIDADA (`t.exports={__esModule:true, ...encoders}`) NO reexportada
  en el límite del módulo. Igual en service (main.js `kn()` es closure-privado). => NO hay
  atajo por "extraer el encoder": la ÚNICA salida observable de la app es la llamada de
  TRANSPORTE. La captura fiel DEBE hacerse en el límite `sendMqttMessage`/`publishDps`.
- Ganancia real: el render-loader YA ejecuta módulos de render headless sin romper
  (base para F4.3, montar componentes).

## Replanteo tras F4.1: saltar "extracción de encoder", ir a captura de transporte
F4.2 (orden de deps) sigue válido. F4.3-F4.5 son el núcleo ahora: montar la página +
puente view↔service + disparar acción + capturar transporte. El encoder no se extrae:
se OBSERVA su efecto (el envelope) en el boundary, que es lo único que la app expone.

## F4.2 — Registro/orden de dependencias de la página
`app-config.json` define rutas/páginas y sus chunks (component `c-*`, template `*.tpl.js`).
- Resolver el grafo de dependencias y cargar en orden (dos pasadas ya ayuda; formalizar).
- Criterio: requerir el módulo de la página del aspirador sin "module not defined".

## F4.3 — Montaje (render) de la página headless [NECESARIO — atajos agotados]
### Atajos descartados (probados, 2026-09-30)
1. Extraer encoders por require → NO exportados (F4.1).
2. Disparar la acción en service via configs de Page()/Component() → capturados 1 App,
   27 Pages, 30 Components (`tools/service_page_capture.js`), pero NINGÚN método de config
   referencia `roomClean`/`setRoomClean`: las páginas son shell de miniprograma (onLoad/
   onShow/data) y la lógica del aspirador está en COMPONENTES FUNCIONALES React (los
   `useContext`/hook `Ej`), que NO pasan por Page()/Component(). Sin disparo service-side.
=> ÚNICA vía: montar el árbol React de la página y simular la interacción.
Conducir `view.js` para renderizar el árbol de la página en el DOM de jsdom.
- Esto instancia los componentes y hooks; el hook de acción `Ej` obtiene su contexto React `dc`.
- Criterio: la página monta sin throw y quedan accesibles los handlers de los controles.

## F4.4 — Puente service↔view (contexto `dc`)
En la app, view (render) y service (lógica) hablan por ViewJSBridge/ServiceJSBridge.
`useContext(dc)` en el componente debe devolver `{useMqtt, commandVersion, devices→store
con .common.model.actions}` derivado del dispositivo real (de host5/device.json).
- Decidir arquitectura: un solo contexto compartido vs. dos contextos con bridge simulado.
- Criterio: dentro del render, `Ej(device).setRoomClean` está disponible y ve el device.

## F4.5 — Disparo de la acción y captura del envelope
Localizar el handler de room clean en la página montada, invocarlo (simular tap) y
capturar el payload en `DeviceKit.sendMqttMessage`/`publishDps` (ya interceptados).
- Criterio: el envelope capturado == el que envía `device_manager._room_clean`
  (reqType roomCleanSet, v1.0.0, protocol 64, ids/suctions/...). Verificación cruzada.

## F4.6 — Generalización a "descriptor de comandos" por producto
Enumerar TODAS las acciones que expone el panel, capturar el transporte de cada una y
emitir un descriptor por productId (capacidad → encoder/transporte) que el plugin consuma
de forma genérica. Integrar en el plugin (sustituye lógica por-dispositivo a mano).
- Criterio: dado un productId nuevo, el plugin deriva sus comandos sin tocar código.

## Regla de seguridad (todas las subfases)
Todo LOCAL (~/dev/tuya-emu/hg, ~/.tuya_native venv), NUNCA producción. No enviar a
hardware real ningún comando derivado hasta verificación cruzada con la implementación
ya probada. Ver [[feedback_no_prod_before_proof]] / [[feedback_tuya_device_control]].

## F4 realización PRAGMÁTICA y FIEL (2026-09-30): derivación de comandos por fuente
Dado que el render React headless es un subsistema desproporcionado y los 3 atajos de
ejecución están agotados, se adopta la vía que YA funcionó para room clean: DERIVAR el
descriptor de comandos leyendo el dispatch de la app en `panel/main.js` (no ejecutar).
- `tools/panel_commands.js <panelDir> [productId]`: parsea main.js y emite JSON con
  proto64_commands ({command=reqType, version, protocol 64, message_fields}) y dp_commands
  ({encoder, args}) + la lista de reqTypes. Es la MISMA lógica de la app, por producto.
- Conga (`docs/command_descriptor_conga.json`): 9 comandos proto64 (roomCleanSet,
  zoneCleanSet, spotCleanSet, restrictedAreaSet, partDivisionSet, partMergeSet,
  deleteMapSet, SaveCurrMapSet, passwordSet) + 5 DP + 17 reqTypes. roomCleanSet coincide
  EXACTO con `device_manager._room_clean` (ya verificado en prod).
- ALCANCE/limite honesto: el descriptor da el ESQUEMA fiel (reqType + campos + transporte).
  Los comandos simples (room clean: ids+defaults) quedan turnkey. Los que dependen de
  geometría de mapa (zone/spot/virtual: `polygons` a partir de coords de la UI) necesitan
  además la transformación de valores de la UI — el esquema está, falta el cálculo de
  polígonos para esos. Para el plugin: expone/implementa comando a comando sobre este
  descriptor, empezando por los que no requieren geometría.
Esto cumple "exactamente como la app, sin encoder a mano" para el dispatch; el render
headless (F4.3) queda como opción futura solo si se quisiera capturar también la geometría
de mapa automáticamente.

## Auto-mapeo + BIDIRECCIONALIDAD verificados en Mac (2026-10-01)
auto_entities.build_entities_auto (entities.build_entities delega en el): auto-mapea entidades
HA solo del thing-model, ingiriendo lo compatible en la entidad final (vacuum: fan_speed/
clean_segments/battery/status; climate: temp/modo/fan/swing/presets; light: brillo/color), resto
por tipo con todas sus caracteristicas. Verificado contra 16 dispositivos reales (sd/dj/kt/qn/ggq/
cz/msp): cada uno auto-detecta dominio y entidades, cero a mano, cero DPs manuales.
COMUNICACION BIDIRECCIONAL verificada en vivo (Conga, no-climate):
- device->HA (dp.get): valores en vivo (sweep_mop_mode=both_work, water_output=high, clean_time=17,
  clean_area=7, edge_brush_life=5771...) = lo que publica el state_topic (value_json.<code>).
- HA->device (publish_dps): volume_set 0->20, readback=20 OK, restaurado a 0.
Llamadas por devId (dp.get/dp.publish) funcionan con la sesion app; group.device.list falla solo
en 2 hogares compartidos sin acceso (no afecta control). NO desplegado a prod (pendiente OK usuario).
