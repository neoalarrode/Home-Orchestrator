# F3 — Despacho de room clean DERIVADO del panel original (no adivinado)

Leído paso a paso del `main.js` del panel del Conga (mini-programa Ray), ejecutado
sin errores en el arnés headless (`tools/panel_host.js`). Cadena real:

```
Ej = function(t){                         // factory-hook del panel
  var n = useContext(dc),                 // contexto React del dispositivo
      r = n.useMqtt,                       // ¿soporta protocolo 64? (nuestro Conga: sí)
      i = n.commandVersion,                // "1.0.0"
      o = n.devices;                       // store: o.common.model.actions.*
  return {
    setRoomClean: function(rooms){
      if (r) {                             // === RAMA proto64 (la que usa el Conga) ===
        // por cada room: ids/suctions/cisterns/cleanCounts/yMops/sweepMopModes
        var h = Ai({ deviceId:t, reqType:"roomCleanSet",
                     message:{ ids, suctions, cisterns, cleanCounts, yMops, sweepMopModes, num } });
        return ty.device.sendMqttMessage(h), query(h.message.taskId);
      }
      // === RAMA legacy (DP command_trans) ===
      var m = encodeRoomClean0x14({version:i, roomIds, cleanTimes});
      return o.common.model.actions.command_trans.set(m);   // -> ty.device.publishDps
    }, ...
  }
}
```

`Ai(...)` (envelope builder, verificado):

```
Ai({deviceId,reqType,message,version?,options?}) => {
  deviceId,
  message: { reqType, version: version||"1.0.0", taskId: String(Date.now()), ...message },
  options: {},
  protocol: 64            // SKe.appToRobot=64 (robotToApp=65)
}
```

## Envelope EXACTO que la app envía para room clean (Conga, rama proto64)

```json
{
  "deviceId": "<devId>",
  "protocol": 64,
  "options": {},
  "message": {
    "reqType": "roomCleanSet",
    "version": "1.0.0",
    "taskId": "<Date.now()>",
    "ids":           [<int por room>],
    "suctions":      [<str o "" por room>],
    "cisterns":      [<por room>],
    "cleanCounts":   [<cleanTimes, default 1>],
    "yMops":         [<default -1>],
    "sweepMopModes": [<default "only_sweep">],
    "num": <nº de rooms>
  }
}
```

`sendMqttMessage` = canal MQTT app; el SDK nativo hace el framing GCM
(header12 + nonce + GCM con la **app localKey**), reimplementado en
`tuya_native/mqtt_transport.py`. **Esto coincide 1:1 con lo que ya envía el plugin**
(`device_manager._room_clean`, ids como int, reqType `roomCleanSet` v"1.0.0") —
verificado en vivo (limpió Salón correctamente).

## Lo que esto demuestra para el motor dinámico

- El panel es un componente React (Ray): las acciones (`setRoomClean`, `setZoneClean`,
  `setVirtualArea/Wall`, `setSpotClean`...) son hooks (`useContext(dc)`) que eligen
  transporte según `useMqtt`: **proto64 `sendMqttMessage`** o **DP `command_trans`→`publishDps`**.
- El arnés carga framework+panel con 0 errores e inyecta DeviceKit (device in) capturando
  `publishDps`/`sendMqttMessage` (transporte out).
- **Falta F4:** shim mínimo de render React que alimente el contexto `dc`
  ({useMqtt, commandVersion, devices→store con model.actions}) para INVOCAR las
  acciones headless y capturar el envelope AUTOMÁTICAMENTE por dispositivo, en vez de
  leerlo a mano. Ese es el motor genérico "Todo lo gordo".
