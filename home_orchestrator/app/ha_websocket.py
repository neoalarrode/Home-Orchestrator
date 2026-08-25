"""
Cliente WebSocket persistente hacia Home Assistant.

Hasta ahora `ha_client.py` habla con HA casi siempre por REST — sirve para
"preguntar", pero no para "que HA avise". Este modulo abre una conexion
WebSocket (`/api/websocket`) y se suscribe a `state_changed`: en cuanto
cambia el estado de un sensor que nos interesa (consumo, solar, SOC,
potencia de baterias declaradas por HA...), HA lo empuja al instante, sin
que el add-on tenga que sondear. Es el mismo mecanismo que ya usan Node-RED
o cualquier custom_component reactivo (como Climate Orchestrator) — la
diferencia es que ellos corren integrados en HA o via WebSocket, y este
add-on, al ser un proceso aparte (Supervisor), necesita abrir la conexion
el mismo.

Diseño deliberadamente simple y con red de seguridad:
  - Reconexion automatica con backoff si se cae la conexion (WiFi, reinicio
    de HA Core, lo que sea) — nunca deja el add-on sin datos por un fallo
    puntual.
  - El ciclo PERIODICO (`background_loop`/`run_cycle` en main.py) sigue
    funcionando exactamente igual, como respaldo — si el WebSocket falla o
    tarda en reconectar, el add-on sigue re-planificando cada
    `cycle_seconds` como siempre lo ha hecho. El WebSocket es una mejora de
    LATENCIA (reaccionar en segundos, no esperar hasta el proximo ciclo),
    nunca una dependencia dura.
  - Nunca lanza `run_cycle()` directamente desde el hilo del WebSocket:
    solo marca "hay algo nuevo que mirar" (un `threading.Event`) y un
    trabajador aparte decide cuando ejecutar de verdad, con un margen
    minimo entre ejecuciones (`REACTIVE_MIN_INTERVAL_SECONDS`) para no
    lanzar el ciclo completo de planificacion decenas de veces por segundo
    si varios sensores cambian casi a la vez.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time

log = logging.getLogger("ha_websocket")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
if SUPERVISOR_TOKEN:
    WS_URL = "ws://supervisor/core/websocket"
    TOKEN = SUPERVISOR_TOKEN
else:
    _ha_url = os.environ.get("HA_URL", "http://localhost:8123/api")
    WS_URL = _ha_url.replace("http://", "ws://").replace("https://", "wss://").removesuffix("/api") + "/api/websocket"
    TOKEN = os.environ.get("HA_TOKEN", "")

# Backoff de reconexion: crece hasta el ultimo valor y se queda ahi (nunca
# deja de reintentar del todo). Una conexion que llega a autenticarse bien
# resetea el contador — un fallo puntual no debe ir acumulando backoff para
# siempre.
RECONNECT_BACKOFF_SECONDS = (2, 5, 10, 30, 60)

# Cuanto tiempo minimo, como poco, entre dos ejecuciones reactivas seguidas
# del ciclo de planificacion — el propio `run_cycle` ya tarda un rato en
# hacer sus llamadas reales a HA/EcoFlow, no tiene sentido relanzarlo antes
# de que la vuelta anterior haya podido terminar y sentar los cambios.
REACTIVE_MIN_INTERVAL_SECONDS = 5

# Clave del consumidor que se registra por el constructor, para no obligar a
# cambiar de golpe todas las llamadas existentes.
_DEFAULT_KEY = "__default__"

_shared_client: "HAWebSocketClient | None" = None
_shared_lock = threading.Lock()


def shared() -> "HAWebSocketClient":
    """La UNICA conexion con HA de todo el addon.

    La abre el core al arrancar y la consumen los plugins: cada uno se
    registra con `subscribe(clave, callback)` y declara sus entidades con
    `set_watched_entities(..., key=clave)`.

    Antes cada plugin abria la suya. Las tres recibian exactamente el mismo
    aluvion -- medido contra una instalacion real: 786 KB/min y 9,3
    eventos/s por conexion, de los que el filtro local tiraba el 97% -- y
    ademas cada una mantenia su propia copia de los ~1700 estados. Tres
    veces el trabajo para el mismo dato.
    """
    global _shared_client
    with _shared_lock:
        if _shared_client is None:
            _shared_client = HAWebSocketClient()
        return _shared_client


def start_shared() -> "HAWebSocketClient":
    """Arranca el lector de la conexion compartida (idempotente)."""
    cliente = shared()
    if not getattr(cliente, "_reader_started", False):
        cliente._reader_started = True
        threading.Thread(target=cliente.run_forever, name="ha-ws", daemon=True).start()
    return cliente


class HAWebSocketClient:
    """Una instancia por add-on. `set_watched_entities` se llama cada vez
    que `run_cycle` recarga la config (baterias/sensores pueden cambiar en
    caliente desde la interfaz) — la suscripcion en si es a TODOS los
    `state_changed` (HA no permite filtrar por entidad en la suscripcion),
    el filtrado a "nos interesa esta o no" se hace aqui, en memoria, barato."""

    def __init__(self, on_relevant_change=None) -> None:
        self._watched: set[str] = set()
        self._watched_lock = threading.Lock()
        # Varios consumidores sobre UNA sola conexion (ver `subscribe`). El
        # callback del constructor sigue admitiendose para no cambiar a la vez
        # todas las llamadas: se registra como uno mas, con la clave por
        # defecto.
        self._listeners: dict[str, object] = {}
        self._watched_by_key: dict[str, set[str]] = {}
        # Lo que se LEE pero no despierta (ver `set_cached_entities`).
        self._cached_by_key: dict[str, set[str]] = {}
        # Conjunto por el que se pregunto en la suscripcion viva, para saber
        # cuando hay que rehacerla (una zona nueva, una regla editada...).
        self._subscribed: set[str] | None = None
        self._sub_id: int | None = None
        # True si HA acepto `subscribe_entities` (filtra el y manda deltas);
        # False si hubo que caer a la suscripcion completa de siempre.
        self._compressed = False
        if on_relevant_change is not None:
            self._listeners[_DEFAULT_KEY] = on_relevant_change
            self._watched_by_key[_DEFAULT_KEY] = set()
        self._ws = None
        self._stop = False
        self.connected = False
        # Peticion/respuesta sobre la MISMA conexion persistente (ver
        # `call`) -- ademas de escuchar eventos, cualquier hilo puede
        # pedir algo puntual (get_states, historico, llamar a un
        # servicio...) y esperar su respuesta, correlacionada por id de
        # mensaje. Todo lo que hable con HA pasa por aqui, nunca por REST
        # aparte -- una unica conexion, un unico transporte.
        self._id_lock = threading.Lock()
        self._next_id = 0
        self._send_lock = threading.Lock()
        self._pending: dict[int, queue.Queue] = {}
        # BUG REAL, confirmado por el usuario: incluso con la latencia de
        # zonas/luces ya arreglada, el encendido seguia tardando 3-5s en
        # TODAS las zonas por igual (Tapo, Tuya, o luces nativas de HA sin
        # ningun bridge de por medio) -- la causa comun era esta: `get_
        # states()` trae el volcado COMPLETO de estados de HA (1770
        # entidades, ~870KB en esta instalacion) por WebSocket, y Lighting
        # lo pedia de nuevo en CADA ciclo reactivo. Ahora se mantiene una
        # copia local (`_states_cache`), sembrada UNA vez al conectar y
        # actualizada en vivo con cada evento `state_changed` que ya nos
        # llega de todos modos (la suscripcion es a TODOS los cambios,
        # filtrados aqui) -- `get_states()`/`get_state()` pasan a ser
        # lecturas locales instantaneas, sin ningun viaje de red.
        self._states_lock = threading.Lock()
        self._states_cache: dict[str, dict] = {}

    def _next_msg_id(self) -> int:
        with self._id_lock:
            self._next_id += 1
            return self._next_id

    def call(self, msg_type: str, timeout: float = 20, **kwargs):
        """Pide algo puntual a HA por la conexion ya abierta y espera su
        respuesta -- bloqueante, pensado para llamarse desde CUALQUIER hilo
        que no sea el propio lector (`run_forever`). Lanza si no hay
        conexion, si HA responde error, o si no responde a tiempo (nunca
        se queda esperando para siempre)."""
        if not self.connected or self._ws is None:
            raise RuntimeError("WebSocket de HA no conectado todavia")
        msg_id = self._next_msg_id()
        q: queue.Queue = queue.Queue(maxsize=1)
        self._pending[msg_id] = q
        payload = {"id": msg_id, "type": msg_type, **kwargs}
        try:
            with self._send_lock:
                self._ws.send(json.dumps(payload))
            try:
                result = q.get(timeout=timeout)
            except queue.Empty:
                raise TimeoutError(f"WebSocket de HA: sin respuesta a '{msg_type}' en {timeout}s")
            if not result.get("success", True):
                raise RuntimeError(f"WebSocket de HA devolvio error para '{msg_type}': {result.get('error')}")
            return result.get("result")
        finally:
            self._pending.pop(msg_id, None)

    # ---------------------------------------------------- atajos comunes --

    def get_states(self) -> list[dict]:
        """Lectura LOCAL de la copia mantenida en vivo (ver `_states_
        cache` en `__init__` y `_connect_and_listen`) -- ya NO pide el
        volcado completo a HA en cada llamada (bug real de latencia,
        confirmado por el usuario). Si el WebSocket aun no ha terminado
        de conectar/sembrar la copia (arranque en frio), devuelve lo que
        haya ahora mismo (vacio -- los llamantes ya manejan bien "sin
        datos todavia", igual que manejaban un fallo de `call()`)."""
        with self._states_lock:
            return list(self._states_cache.values())

    def get_state(self, entity_id: str) -> dict | None:
        with self._states_lock:
            return self._states_cache.get(entity_id)

    def call_service(self, domain: str, service: str, service_data: dict | None = None,
                      target: dict | None = None, return_response: bool = False):
        kwargs = {"domain": domain, "service": service}
        if service_data:
            kwargs["service_data"] = service_data
        if target:
            kwargs["target"] = target
        if return_response:
            kwargs["return_response"] = True
        result = self.call("call_service", **kwargs)
        return result.get("response") if return_response and result else None

    def get_history(self, entity_id: str, start_iso: str, with_attributes: bool = False) -> list[dict]:
        """
        Historico de UNA entidad desde `start_iso` hasta ahora, normalizado
        a una lista de puntos `{"state", "last_updated", "attributes"}` —
        oculta el formato compacto real del WebSocket (`history/
        history_during_period`, claves "s"/"lu"/"a") a quien llama.

        OJO con `with_attributes=True`: cada punto solo trae los
        atributos que CAMBIARON respecto al anterior (formato comprimido
        de HA), no el diccionario completo — aqui se rellenan hacia
        adelante (el primer punto SI trae el conjunto completo, los
        siguientes se van fusionando encima) para que quien llama siempre
        vea el estado de atributos COMPLETO en cada punto, nunca uno a
        medias.
        """
        result = self.call(
            "history/history_during_period",
            start_time=start_iso,
            entity_ids=[entity_id],
            minimal_response=not with_attributes,
            no_attributes=not with_attributes,
            significant_changes_only=False,
        )
        raw = (result or {}).get(entity_id) or []
        points = []
        known_attrs: dict = {}
        for p in raw:
            if with_attributes:
                known_attrs = {**known_attrs, **(p.get("a") or {})}
                attrs = dict(known_attrs)
            else:
                attrs = {}
            points.append({"state": p.get("s"), "last_updated": p.get("lu"), "attributes": attrs})
        return points

    def subscribe(self, key: str, on_change) -> None:
        """Registra un consumidor. `key` identifica al plugin, para que cada
        uno declare SUS entidades sin pisar las de los demas.

        Una sola conexion para todo el addon: antes cada plugin abria la
        suya, y las tres recibian el MISMO aluvion completo de cambios de
        estado. Medido contra una instalacion real: 786 KB/min por conexion,
        de los que el filtro local tiraba el 97% -- multiplicado por tres.
        """
        with self._watched_lock:
            self._listeners[key] = on_change
            self._watched_by_key.setdefault(key, set())

    def set_watched_entities(self, entities: set[str], key: str | None = None) -> None:
        """Entidades que le importan a UN consumidor.

        `key=None` mantiene la firma de antes (un solo consumidor, el que se
        paso al constructor). Con la conexion compartida, cada plugin usa su
        propia clave: esto SUSTITUYE su conjunto, no el de todos -- que es lo
        que pasaria si siguiera habiendo un unico `_watched` global.
        """
        clave = key or _DEFAULT_KEY
        with self._watched_lock:
            self._watched_by_key[clave] = {e for e in entities if e}
            self._watched = set().union(*self._watched_by_key.values()) if self._watched_by_key else set()

    def set_cached_entities(self, entities: set[str], key: str) -> None:
        """Entidades cuyo estado hay que tener FRESCO, pero que no disparan.

        Hacen falta dos conjuntos, no uno. `set_watched_entities` dice "esto
        me despierta"; esto dice "esto lo LEO". Las luces son el ejemplo: no
        queremos recalcular una zona porque su propia bombilla haya cambiado
        (seria un bucle), pero su estado real si se consulta -- para saber si
        ya esta encendida y para detectar que alguien la toco a mano.

        Antes daba igual, porque se recibia el aluvion entero y el caché
        estaba fresco por accidente. Al pedirle a HA que filtre, lo que no se
        declare aqui se queda con el valor del volcado inicial y envejece en
        silencio -- y `respect_manual_changes` empezaria a fallar sin que
        nadie supiera por que.
        """
        with self._watched_lock:
            self._cached_by_key[key] = {e for e in entities if e}

    def _subscription_entities(self) -> set[str]:
        """Lo que se le pide a HA que nos mande: lo que despierta MAS lo que
        se lee."""
        with self._watched_lock:
            todo: set[str] = set(self._watched)
            for s in self._cached_by_key.values():
                todo |= s
            return todo

    def watched_entities(self) -> set[str]:
        """Union de lo que vigilan todos los consumidores."""
        with self._watched_lock:
            return set(self._watched)

    def _is_watched(self, entity_id: str) -> bool:
        with self._watched_lock:
            return entity_id in self._watched

    def _notify(self, entity_id: str, new_state: dict) -> None:
        """Avisa SOLO a quien vigila esa entidad. Sin esto, con la conexion
        compartida cada plugin se despertaria por los cambios de los otros y
        se perderia justo lo que se gana al filtrar."""
        with self._watched_lock:
            destinos = [
                cb for clave, cb in self._listeners.items()
                if entity_id in self._watched_by_key.get(clave, ())
            ]
        for cb in destinos:
            try:
                cb(entity_id, new_state)
            except Exception:
                log.exception("Fallo notificando el cambio de %s a un consumidor", entity_id)

    def stop(self) -> None:
        self._stop = True
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass

    def run_forever(self) -> None:
        """Bucle de conexion — pensado para correr en su propio hilo daemon,
        para siempre (hasta que el proceso del add-on termine)."""
        attempt = 0
        while not self._stop:
            try:
                self._connect_and_listen()
                attempt = 0  # conexion que llego a autenticarse: resetea el backoff
            except Exception:
                log.warning("WebSocket de HA: conexion perdida (%s), reintentando", "sin detalle")
                log.debug("Detalle del fallo de WebSocket", exc_info=True)
            finally:
                self.connected = False
            if self._stop:
                return
            delay = RECONNECT_BACKOFF_SECONDS[min(attempt, len(RECONNECT_BACKOFF_SECONDS) - 1)]
            attempt += 1
            time.sleep(delay)

    def _resubscribe_locked(self) -> None:
        """(Re)suscribe pidiendole a HA que filtre EL en su lado.

        Solo puede llamarlo el hilo lector: manda y espera el ack por el mismo
        socket, sin pasar por `call()`.

        `subscribe_entities` es la diferencia entre recibirlo todo y recibir
        lo nuestro. Medido contra la instalacion del usuario, en paralelo y
        sobre la misma ventana: 628 KB/min con `subscribe_events` frente a
        3 KB/min con esto -- 214 veces menos, porque ademas de filtrar manda
        DELTAS (solo los campos que cambian) en vez del estado viejo y el
        nuevo enteros con todos sus atributos.

        Si HA no lo admite (version antigua) se cae a la suscripcion de
        siempre: peor, pero funcionando.
        """
        objetivo = self._subscription_entities()
        if self._subscribed is not None:
            anterior_id = self._sub_id
            if anterior_id is not None:
                try:
                    self._ws.send(json.dumps({
                        "id": self._next_msg_id(), "type": "unsubscribe_events",
                        "subscription": anterior_id,
                    }))
                except Exception:
                    log.debug("No se ha podido cancelar la suscripcion anterior", exc_info=True)

        sub_id = self._next_msg_id()
        if objetivo:
            self._ws.send(json.dumps({
                "id": sub_id, "type": "subscribe_entities", "entity_ids": sorted(objetivo),
            }))
            ack = json.loads(self._ws.recv())
            if ack.get("success"):
                self._sub_id, self._subscribed, self._compressed = sub_id, set(objetivo), True
                log.info(
                    "WebSocket de HA: suscrito a %d entidad(es) concretas -- HA filtra en su lado",
                    len(objetivo),
                )
                return
            log.warning(
                "WebSocket de HA: `subscribe_entities` rechazado (%s) -- se usa la suscripcion "
                "completa, mas cara", ack.get("error"),
            )
            sub_id = self._next_msg_id()

        self._ws.send(json.dumps({"id": sub_id, "type": "subscribe_events", "event_type": "state_changed"}))
        ack = json.loads(self._ws.recv())
        if not ack.get("success"):
            raise RuntimeError(f"No se pudo suscribir a state_changed: {ack}")
        self._sub_id, self._subscribed, self._compressed = sub_id, set(objetivo), False

    def _apply_compressed(self, event: dict) -> list[tuple[str, dict, str | None]]:
        """Traduce el formato comprimido de `subscribe_entities` al mismo que
        usa el resto del codigo, y actualiza el cache.

        Devuelve (entity_id, estado_nuevo, estado_viejo) de cada cambio, para
        que el llamante decida a quien avisar.

        Formato: `a` altas (completas), `c` cambios (`+` lo que cambia, `-` lo
        que se quita), `r` bajas. Los campos van abreviados: `s` estado,
        `a` atributos, `lc`/`lu` marcas de tiempo.
        """
        salida: list[tuple[str, dict, str | None]] = []
        with self._states_lock:
            for entity_id, comp in (event.get("a") or {}).items():
                self._states_cache[entity_id] = {
                    "entity_id": entity_id, "state": comp.get("s"),
                    "attributes": comp.get("a") or {},
                    "last_changed": comp.get("lc"), "last_updated": comp.get("lu"),
                }
            for entity_id in (event.get("r") or []):
                self._states_cache.pop(entity_id, None)
            for entity_id, cambio in (event.get("c") or {}).items():
                actual = self._states_cache.get(entity_id)
                anterior = (actual or {}).get("state")
                if actual is None:
                    actual = {"entity_id": entity_id, "state": None, "attributes": {}}
                nuevo = dict(actual)
                mas = cambio.get("+") or {}
                if "s" in mas:
                    nuevo["state"] = mas["s"]
                if "a" in mas:
                    # Los atributos vienen SOLO los que cambian: se funden con
                    # los que ya habia, no se sustituyen -- reemplazarlos
                    # borraria todo lo que no viniera en este delta.
                    nuevo["attributes"] = {**(actual.get("attributes") or {}), **(mas["a"] or {})}
                if "lc" in mas:
                    nuevo["last_changed"] = mas["lc"]
                if "lu" in mas:
                    nuevo["last_updated"] = mas["lu"]
                for quitado in (cambio.get("-") or {}).get("a", []) if isinstance(cambio.get("-"), dict) else []:
                    nuevo.get("attributes", {}).pop(quitado, None)
                self._states_cache[entity_id] = nuevo
                salida.append((entity_id, nuevo, anterior))
        return salida

    def _connect_and_listen(self) -> None:
        import websocket as ws_lib

        self._ws = ws_lib.create_connection(WS_URL, timeout=30)
        try:
            hello = json.loads(self._ws.recv())
            if hello.get("type") != "auth_required":
                raise RuntimeError(f"Handshake inesperado del WebSocket de HA: {hello}")
            self._ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
            auth_result = json.loads(self._ws.recv())
            if auth_result.get("type") != "auth_ok":
                raise RuntimeError(f"Autenticacion WebSocket de HA fallida: {auth_result}")

            # El volcado inicial siembra el cache ENTERO (ver mas abajo), asi
            # que los selectores de la interfaz siguen viendo todas las
            # entidades de HA aunque la suscripcion viva vaya filtrada.
            self._resubscribe_locked()

            # Siembra UNA vez la copia local completa -- directo por
            # `recv()`, NO via `call()` (que esperaria la respuesta desde
            # ESTE MISMO hilo lector, un interbloqueo seguro: nadie mas
            # va a leer el socket para entregarsela). En este punto de la
            # conexion (justo tras el ack de suscripcion, antes de que
            # ningun otro hilo haya podido mandar nada) el SIGUIENTE
            # mensaje que llegue solo puede ser esta respuesta.
            states_id = self._next_msg_id()
            self._ws.send(json.dumps({"id": states_id, "type": "get_states"}))
            states_resp = json.loads(self._ws.recv())
            if states_resp.get("success"):
                with self._states_lock:
                    self._states_cache = {
                        s["entity_id"]: s for s in (states_resp.get("result") or []) if s.get("entity_id")
                    }
            else:
                log.warning("WebSocket de HA: fallo sembrando la copia local de estados: %s", states_resp.get("error"))

            self.connected = True
            log.info(
                "WebSocket de HA conectado y suscrito a state_changed (%d entidades sembradas)",
                len(self._states_cache),
            )

            while not self._stop:
                try:
                    raw = self._ws.recv()
                except ws_lib.WebSocketTimeoutException:
                    # CON FILTRO, el silencio es lo NORMAL: antes llegaban 9
                    # eventos por segundo de toda la casa y el timeout del
                    # socket no saltaba jamas, asi que tratarlo como error
                    # nunca se noto. Pidiendole a HA que filtre pueden pasar
                    # minutos sin nada nuestro, y reconectar por eso seria
                    # peor que el problema que se venia a resolver.
                    #
                    # De paso es el momento natural para rehacer la
                    # suscripcion si han cambiado las entidades (una zona
                    # nueva, una regla editada).
                    if self._subscribed is not None and self._subscription_entities() != self._subscribed:
                        self._resubscribe_locked()
                    continue
                if not raw:
                    raise RuntimeError("WebSocket de HA cerrado por el otro lado")
                msg = json.loads(raw)
                msg_type = msg.get("type")

                if msg_type == "result":
                    # Respuesta a una llamada puntual hecha desde OTRO hilo
                    # (ver `call`) -- se entrega a quien esperaba ese id
                    # concreto, nunca se procesa aqui mismo.
                    q = self._pending.get(msg.get("id"))
                    if q is not None:
                        try:
                            q.put_nowait(msg)
                        except queue.Full:
                            pass
                    continue

                if msg_type != "event":
                    continue
                event = msg.get("event") or {}

                if self._compressed:
                    # `subscribe_entities`: HA ya ha filtrado, y lo que llega
                    # son deltas. Todo lo suscrito entra al cache; solo
                    # despierta a alguien lo que ESE alguien vigile.
                    for entity_id, nuevo, anterior in self._apply_compressed(event):
                        if not self._is_watched(entity_id):
                            continue
                        if anterior == nuevo.get("state"):
                            continue  # solo cambio un atributo, no es lectura nueva
                        self._notify(entity_id, nuevo.get("state"))
                    continue

                if event.get("event_type") != "state_changed":
                    continue
                data = event.get("data") or {}
                entity_id = data.get("entity_id")
                if not entity_id:
                    continue
                new_state_obj = data.get("new_state")
                # La copia local se mantiene con TODOS los cambios, no
                # solo los "vigilados" -- seguimos suscritos a TODO
                # `state_changed` (HA no permite filtrar por entidad en
                # la suscripcion, ver docstring de la clase), asi que
                # esto no cuesta ninguna llamada de red extra, solo
                # actualizar el dict local. El filtro "vigilado o no"
                # (mas abajo) sigue existiendo tal cual -- decide si esto
                # dispara un ciclo reactivo, nunca si se guarda o no.
                with self._states_lock:
                    if new_state_obj is None:
                        self._states_cache.pop(entity_id, None)
                    else:
                        self._states_cache[entity_id] = new_state_obj
                if not self._is_watched(entity_id):
                    continue
                old_state = (data.get("old_state") or {}).get("state")
                new_state = new_state_obj.get("state") if new_state_obj else None
                if old_state == new_state:
                    # Solo el ATRIBUTO cambio (p.ej. jitter interno de otra
                    # integracion) — no es una lectura nueva de verdad,
                    # ignorarlo evita relanzar el ciclo por nada.
                    continue
                self._notify(entity_id, new_state)
        finally:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
            # Cualquier `call()` en espera no se queda colgada hasta su
            # propio timeout si la conexion se cae entera -- se entera YA
            # de que ha fallado.
            for q in list(self._pending.values()):
                try:
                    q.put_nowait({"success": False, "error": {"message": "conexion WebSocket perdida"}})
                except queue.Full:
                    pass


class ReactiveTrigger:
    """Debounce + coalesce de disparos reactivos: cualquier numero de
    eventos que lleguen mientras se espera o se esta ejecutando el ciclo se
    reducen a UNA sola ejecucion mas, justo despues del margen minimo — ni
    se pierde ningun cambio real (si algo cambio durante la espera, se
    vuelve a ejecutar), ni se satura `run_cycle` con ejecuciones
    superpuestas.

    `min_interval_seconds` es configurable por instancia (antes era un
    valor fijo global, `REACTIVE_MIN_INTERVAL_SECONDS`) -- BUG REAL,
    confirmado por el usuario: Lighting comparte esta misma clase con
    Battery, cuyo `run_cycle` SI hace llamadas externas caras (EcoFlow,
    forecast) y necesita ese margen de 5s para no saturar nada. Lighting
    no tiene ese coste (decidir y encender una zona es barato, todo en
    proceso/LAN) y el usuario esperaba una reaccion inmediata a la
    presencia, igual que tenia con Node-RED -- con el margen de 5s
    heredado de Battery, si CUALQUIER otra entidad vigilada (de
    cualquier zona) cambiaba justo antes de detectarse presencia, el
    encendido real quedaba esperando el resto de ese margen antes de
    poder procesarse. Battery/Climate mantienen el margen por defecto
    (comportamiento sin cambios); Lighting pasa uno mucho mas bajo."""

    def __init__(self, run_once, min_interval_seconds: float = REACTIVE_MIN_INTERVAL_SECONDS) -> None:
        self._run_once = run_once
        self._min_interval_seconds = min_interval_seconds
        self._event = threading.Event()
        self._lock = threading.Lock()

    def trigger(self) -> None:
        self._event.set()

    def worker_loop(self) -> None:
        while True:
            self._event.wait()
            self._event.clear()
            with self._lock:
                try:
                    self._run_once()
                except Exception:
                    log.exception("Fallo en la ejecucion reactiva del ciclo de planificacion")
            time.sleep(self._min_interval_seconds)
