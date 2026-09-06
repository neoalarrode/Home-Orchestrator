# Tareas pendientes -- Home Orchestrator

Última actualización: sesión del 2026-09-06. Este fichero es para retomar
el trabajo si se pierde el contexto de la conversación -- no forma parte
del producto, no hace falta desplegarlo ni versionarlo con cuidado.

Todo lo que estaba en la versión anterior de este fichero (sesión del
2026-08-15) y aparecía marcado "HECHO Y DESPLEGADO" o que se ha podido
confirmar resuelto contra el repo/GitHub real en esta sesión se ha
retirado -- ver `CHANGELOG.md` para el historial completo. Lo que queda
abajo es SOLO lo verificado como todavía abierto ahora mismo.

## Contexto general

Repo: `neoalarrode/Home-Orchestrator`. Deploy real en `root@192.168.1.93`
(SSH, ver memoria `ha_host_ssh_root_password`), addon
`bfadc9b2_home_orchestrator`. Verificación contra HA real en
`https://haos.ericlarrode.com` (token en memoria `ha_long_lived_token`).

Patrón de despliegue de un plugin YA EXISTENTE (repetido en todo el
proyecto): bump `version` en su `*_plugin.py` + entrada en CHANGELOG.md
-> commit/push -> `git tag vX.Y.Z` + push -> descargar el tarball del
tag, `shasum -a 256` -> actualizar `tag`/`sha256`/`version` de ese plugin
en `plugins.json` (raíz del repo) -> commit/push -> en el host: `docker
exec app_bfadc9b2_home_orchestrator rm -f /data/plugins/manifest.json &&
ha addons restart bfadc9b2_home_orchestrator` -> verificar en
`docker logs | grep -iE "plugin cargado|error"`.

Un plugin genuinamente NUEVO (slug que no ha existido nunca) necesita
ADEMÁS una release del core primero (bump `config.yaml` + añadir el slug
a `PLUGIN_CATALOG` Y `PLUGIN_REGISTRY` en `plugin_loader.py` -> `ha store
reload && ha addons update bfadc9b2_home_orchestrator`, rebuild real de
Docker) antes de que el paso anterior funcione -- ver CHANGELOG 0.77.34/
0.77.36 para el detalle completo de por qué.

**Ojo con `raw.githubusercontent.com`**: tiene su propio cache CDN
(unos minutos) independiente de la API de GitHub -- si el addon sigue
viendo el tag/versión viejos tras un push real (confirmable con `curl
https://api.github.com/repos/.../contents/plugins.json`, que SÍ es
fresco), es esa caché, no un fallo del despliegue. Esperar y reintentar.

## Abierto -- CodeQL (`gh api repos/neoalarrode/Home-Orchestrator/code-scanning/alerts`)

Verificado en vivo esta sesión (18 stack-trace-exposure y 3 path-
injection ya en `fixed`, 8 weak-cryptographic-algorithm en `dismissed`
con justificación -- protocolo LAN real de Tuya, no elegible). Quedan
abiertas:

- **`py/stack-trace-exposure` x2**: `home_orchestrator/app/main.py:1981`
  y `:3023` -- mismo patrón ya corregido en el resto del proyecto
  (`jsonify({"error": str(exc)})` expone detalle interno; cambiar a
  loggear con `exc_info=True` y devolver un mensaje genérico).
- **`py/bind-socket-all-network-interfaces` x1**:
  `home_orchestrator/app/govee/device_manager.py:234` -- sin investigar
  todavía si es evitable (puede que el descubrimiento LAN de Govee
  necesite escuchar en todas las interfaces a propósito, igual que el
  `weak-cryptographic-algorithm` de Tuya) o si se puede acotar.

## Abierto -- Covers Orchestrator (nuevo esta sesión, v0.6.0 en producción)

- **Aprendizaje de orientación/% de protección solar** (Salón y
  Despacho, ambas con `auto_learn_window_orientation_enabled` +
  `auto_learn_sun_protection_enabled` activos): necesita ~10 días de
  histórico real con las persianas abiertas antes de que deje de
  mostrar "aprendiendo…" -- nada que hacer salvo esperar y comprobar
  más adelante.
- **`cover.ventana_habitacion_invitados` (zona Despacho) en
  `unavailable`** en HA -- probable fallo de batería/Zigbee del propio
  dispositivo, no de software. Ninguna regla de Covers podrá moverla
  mientras siga así. Pendiente de que el usuario lo revise físicamente.
- Interfaz reescrita (v0.6.0) para alinearse con el resto (design-system
  compartido, editar zonas en vez de solo crear) -- verificado servido
  correctamente en producción, pero NO probado a mano en el navegador
  (crear/editar/guardar una zona de verdad desde la UI).

## Abierto -- auditoría de entidades muertas

175 entidades confirmadas como muertas en toda la instancia (verificado
con histórico real de 5 días, agrupadas por origen: Tado, Xiaomi Miot,
Matter/Aqara, Meross, móviles, varios) -- **pendiente de que el usuario
decida** si se eliminan todas de una vez o se revisan los grupos grandes
uno a uno antes.

## Otras notas sueltas

- Credenciales/tokens usados esta sesión están en memoria persistente
  (`ha_host_ssh_root_password`, `ha_long_lived_token`, credenciales de
  EcoFlow/Govee/Tuya/Grafana) -- no hace falta volver a pedirlos.
- Plan pendiente en `/Users/ericlarrode/.claude/plans/radiant-snuggling-neumann.md`:
  cuatro controles adicionales de EcoFlow (reserva de emergencia,
  vertido a red, salidas AC manuales, límite de importación de red
  automático) + hacer el motor de Energy más reactivo (enganchar el feed
  MQTT de EcoFlow al mismo disparador reactivo que ya usan los sensores
  de HA, debounce de comando) -- diseño completo, cero código escrito
  todavía.
