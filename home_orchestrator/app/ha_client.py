"""
Cliente minimo para hablar con Home Assistant.

Dentro de un addon, HA Supervisor inyecta SUPERVISOR_TOKEN y el proxy
interno en http://supervisor/core/api/. Para desarrollo local fuera del
addon, se puede usar HA_URL + HA_TOKEN (token de larga duracion) en su lugar.
"""

from __future__ import annotations

import logging
import os
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger("ha_client")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
if SUPERVISOR_TOKEN:
    BASE_URL = "http://supervisor/core/api"
    TOKEN = SUPERVISOR_TOKEN
else:
    BASE_URL = os.environ.get("HA_URL", "http://localhost:8123/api")
    TOKEN = os.environ.get("HA_TOKEN", "")

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
TIMEOUT = 10

# Conexion HTTP reutilizada (keep-alive) en vez de abrir una nueva por
# llamada: el ciclo de planificacion lee varios sensores cada vez y se ejecuta
# muy a menudo (ciclo reactivo). Una sesion POR HILO -- `requests.Session` no
# garantiza ser segura compartida entre hilos.
_http_local = threading.local()


def _http() -> requests.Session:
    session = getattr(_http_local, "session", None)
    if session is None:
        session = requests.Session()
        # Un reintento SOLO de conexion (no de lectura): cubre una conexion
        # keep-alive que el otro extremo ya cerro, sin repetir una peticion
        # que si llego a enviarse.
        adapter = requests.adapters.HTTPAdapter(max_retries=1)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        _http_local.session = session
    return session


class HAError(Exception):
    pass


def get_state(entity_id: str):
    r = _http().get(f"{BASE_URL}/states/{entity_id}", headers=HEADERS, timeout=TIMEOUT)
    if r.status_code == 404:
        raise HAError(f"Entidad no encontrada: {entity_id}")
    r.raise_for_status()
    return r.json()


def get_all_states() -> list[dict]:
    """
    Todos los estados de HA de una vez (para descubrir entidades por
    atributo, p.ej. las zonas de Climate Orchestrator - ver
    climate_link.py - en vez de tener que declararlas una a una a mano).
    Lista vacia si HA no responde, nunca propaga la excepcion: quien
    descubre algo a partir de esto ya sabe tratar "no hay nada todavia"
    igual que "no se pudo preguntar ahora".
    """
    try:
        r = requests.get(f"{BASE_URL}/states", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.RequestException:
        return []


def render_template(template: str) -> str | None:
    """
    Pide a HA que renderice una plantilla Jinja2 EL MISMO (POST
    /api/template) — HA solo serializa lo que la plantilla pida, nunca el
    volcado completo de /api/states. Se usa para descubrir las zonas de
    Climate Orchestrator filtrando por dominio "climate" DENTRO de HA en
    vez de traerse las ~2000+ entidades de toda la instalacion para
    filtrarlas aqui (ver climate_link.py) - mismo resultado, fraccion del
    coste, tanto de red como de CPU/memoria en el lado de HA Core.
    Devuelve None si HA no responde (quien lo use ya sabe caer a "no hay
    nada todavia", igual que con `get_all_states`).
    """
    try:
        r = requests.post(f"{BASE_URL}/template", headers=HEADERS, json={"template": template}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except requests.RequestException:
        return None


STALE_STATES = {"unavailable", "unknown", "none", ""}


def get_numeric_state(entity_id: str, default: float | None = 0.0) -> float | None:
    """
    Devuelve el valor numerico de una entidad. Si la entidad esta
    'unavailable'/'unknown' o no existe, devuelve `default` (que puede ser
    None para que el llamante decida saltarse esa entidad en vez de
    asumir un valor inventado). Un fallo de red/HA pasajero (timeout, 502/503
    del Supervisor) tambien cae a `default` en vez de tumbar el ciclo entero
    de planificacion - mejor una hora con un dato por defecto que ninguna
    orden de carga/descarga hasta que HA vuelva a responder.
    """
    try:
        s = get_state(entity_id)["state"]
        if s.strip().lower() in STALE_STATES:
            return default
        return float(s)
    except (HAError, ValueError, KeyError, requests.RequestException):
        return default


def call_service(
    domain: str, service: str, entity_id: str | None = None, extra: dict | None = None,
    timeout: float = TIMEOUT, return_response: bool = False,
):
    payload = {}
    if entity_id:
        payload["entity_id"] = entity_id
    if extra:
        payload.update(extra)
    url = f"{BASE_URL}/services/{domain}/{service}"
    if return_response:
        # Query param que expone HA para servicios que declaran datos de
        # respuesta (SupportsResponse.OPTIONAL/ONLY) — sin esto la llamada
        # funciona igual pero el resultado nunca trae "service_response".
        url += "?return_response"
    r = requests.post(url, headers=HEADERS, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def call_service_with_response(
    domain: str, service: str, extra: dict | None = None, timeout: float = TIMEOUT,
) -> dict | None:
    """
    Para servicios de terceros (p.ej. el puente BLE de EcoFlow, ver
    ecoflow_ble.py) que devuelven datos de verdad, no solo cambian
    entidades — nunca lanza por un fallo de red/HA, devuelve `None` para
    que quien llame lo trate igual que "sin dato todavia" en vez de tumbar
    el ciclo entero.
    """
    try:
        result = call_service(domain, service, extra=extra, timeout=timeout, return_response=True)
    except (HAError, requests.RequestException) as e:
        log.warning(f"Fallo al llamar al servicio {domain}.{service}: {e}")
        return None
    return result.get("service_response") if isinstance(result, dict) else None


def turn_on(entity_id: str):
    domain = entity_id.split(".")[0]
    return call_service(domain, "turn_on", entity_id)


def turn_off(entity_id: str):
    domain = entity_id.split(".")[0]
    return call_service(domain, "turn_off", entity_id)


def set_number(entity_id: str, value: float):
    return call_service("number", "set_value", entity_id, {"value": value})


def publish_sensor(entity_id: str, state, attributes: dict | None = None):
    """Publica un sensor propio del orquestador en HA (para dashboards)."""
    payload = {"state": state, "attributes": attributes or {}}
    r = _http().post(f"{BASE_URL}/states/{entity_id}", headers=HEADERS, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _history_window(entity_id: str, start: datetime, end: datetime, minimal: bool) -> list[dict]:
    """Historico de UNA entidad entre `start` y `end` (UTC).

    BUG REAL, medido contra la instalacion del usuario: `/api/history/period/
    <inicio>` SIN `end_time` no devuelve "desde <inicio> hasta ahora" sino UN
    SOLO DIA a partir de <inicio>. Pedir "los ultimos 10 dias" devolvia nada
    mas que el dia de hace 10 dias (comprobado: 11.064 puntos, todos del
    21-22/09 pidiendo el 01/10). Toda la "media por hora de los ultimos N
    dias" era en realidad el perfil de UN dia suelto, distinto cada dia, y
    los cubos de fin de semana o de laborable se quedaban vacios segun en que
    dia de la semana cayera ese unico dia.

    La marca de tiempo de inicio va EMBEBIDA en la ruta (no en la query), asi
    que tiene que ir "limpia": `.isoformat()` produce "...+00:00" y el "+"
    rompe la ruta. Formato con sufijo "Z". `end_time` va como parametro de
    query y `requests` lo codifica solo.
    """
    params = {"filter_entity_id": entity_id, "end_time": end.strftime("%Y-%m-%dT%H:%M:%SZ")}
    if minimal:
        params["minimal_response"] = "true"
    r = _http().get(
        f"{BASE_URL}/history/period/{start.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        headers=HEADERS, params=params, timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return data[0] if data else []


def _history_days(entity_id: str, days: int, minimal: bool) -> list[dict]:
    """Los ultimos `days` dias, pedidos DIA A DIA y concatenados en orden.

    Un sensor de potencia que reporta cada segundo son ~10.000 puntos al dia:
    diez dias de golpe son una respuesta de varios MB que HA tiene que montar
    de una vez. Troceado, cada peticion es pequeña y rapida, y un trozo que
    falle no tira los demas. Si fallan TODOS se propaga el ultimo error, para
    que quien llama distinga "HA no responde" de "no hay historico".
    """
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    last_error: Exception | None = None
    ok = 0
    for d in range(max(1, int(days)), 0, -1):
        start = now - timedelta(days=d)
        end = now - timedelta(days=d - 1)
        try:
            out.extend(_history_window(entity_id, start, end, minimal))
            ok += 1
        except requests.RequestException as e:
            last_error = e
            if ok == 0:
                # El PRIMER trozo ya falla: HA no esta respondiendo. No se
                # insiste con los demas dias (cada uno esperaria su timeout
                # entero, con el ciclo de planificacion parado mientras).
                break
    if ok == 0 and last_error is not None:
        raise last_error
    return out


def get_history_with_attributes(entity_id: str, days: int) -> list[dict]:
    """
    Igual que `get_history`, pero SIN "minimal_response" -- cada punto trae
    sus atributos completos, no solo el primero. Mas caro (respuesta mucho
    mayor), asi que solo se usa cuando de verdad hace falta leer un
    atributo del historico (p.ej. `hvac_action` de un climate.* delegado,
    ver climate/thermal_model.py) -- para el resto, `get_history` (con
    minimal_response) es mas barato y suficiente.
    """
    return _history_days(entity_id, days, minimal=False)


def get_history(entity_id: str, days: int) -> list[dict]:
    """Cambios de estado de los ultimos `days` dias (ver `_history_window`
    para el bug de "solo un dia" que esto corrige)."""
    return _history_days(entity_id, days, minimal=True)


# Mismo problema y mismo criterio que `_plausible_power_w` en main.py (no se
# puede importar de aqui: ha_client es un modulo de mas bajo nivel que
# main.py importa, no al reves -- mismo motivo de duplicacion ya
# documentado para IMPLAUSIBLE_POWER_CEILING_W en energy_recovery.py).
# Todos los sensores que hoy pasan por `_hourly_avg_by_hour_of_day` son de
# potencia (W): consumo base, solar, potencia de bateria, red en bruto.
# Sin este techo, UNA SOLA lectura disparatada que ya quedo grabada en el
# historico de HA (el mismo tipo de glitch de sensor que `_plausible_power_w`
# filtra en las lecturas EN VIVO, pero que aqui entraba sin ningun filtro)
# contaminaba la previsión de esa franja horaria durante `days` dias
# enteros -- confirmado en pruebas: un solo glitch de ~55kW mezclado con 20
# muestras normales de 450W dispara la media a mas de 3000W (577% de mas)
# hasta que el glitch envejece fuera de la ventana de historico. Igual que
# la lectura en vivo, una muestra por encima del techo se DESCARTA entera
# (nunca se recorta al techo, que seguiria siendo un dato inventado).
IMPLAUSIBLE_POWER_CEILING_W = 30000


def _safe_get_history(entity_id: str, days: int) -> list[dict]:
    """
    Igual que `get_history`, pero absorbe fallos de red/HA (timeouts, 502/503
    del Supervisor mientras arranca o se reinicia HA...) devolviendo lista
    vacia en vez de propagar la excepcion - un ciclo de planificacion entero
    no debe abortar (dejando la bateria sin ninguna orden) solo porque UNA
    llamada de historico haya fallado de forma pasajera.
    """
    try:
        return get_history(entity_id, days)
    except requests.RequestException:
        return []


_has_history_cache: dict[tuple, tuple[float, bool]] = {}
HAS_HISTORY_CACHE_SECONDS = 1800  # 30 min


def has_recent_history(entity_id: str, days: int = 1) -> bool:
    """
    Comprobacion de si hay algun punto de historico real para este sensor
    en los ultimos `days` dias. Se usa para saber si merece la pena intentar
    calcular una media horaria (`hourly_average_forecast`) o si el sensor es
    demasiado nuevo y todavia no hay nada que promediar. OJO: esto NO
    garantiza que cada hora tenga suficiente muestra - eso lo decide
    `hourly_average_forecast_with_reliability` franja a franja.

    Cacheada `HAS_HISTORY_CACHE_SECONDS`: "¿tiene ya historico?" no puede
    cambiar mas que de False a True (nunca al reves, en uso normal), asi
    que no hace falta volver a pedir el historico entero de un sensor -
    potencialmente con muchisimos puntos si reporta muy a menudo, como un
    sensor de potencia solar - en cada ciclo de 30-60s solo para esta
    comprobacion booleana. Sin cache, esto llegaba a pedir el historico
    completo del sensor solar decenas de miles de veces al dia.
    """
    cache_key = (entity_id, days)
    now_ts = time.time()
    cached = _has_history_cache.get(cache_key)
    if cached is not None and (now_ts - cached[0]) < HAS_HISTORY_CACHE_SECONDS:
        return cached[1]
    result = bool(_safe_get_history(entity_id, days))
    _has_history_cache[cache_key] = (now_ts, result)
    return result


# Cuanto se reutiliza la media por hora-del-dia ya calculada antes de
# volver a pedir el historico a HA. Estas medias apenas cambian de un ciclo
# a otro (se basan en dias enteros de historico); pedirlas enteras cada
# `cycle_seconds` es puro peso extra sobre el recorder de HA sin ganar nada
# en precision. Se cachea SOLO la parte cara (pedir y recorrer el historico),
# nunca el resultado ya alineado a "ahora" - la alineacion cambia cada hora.
_HISTORY_CACHE_SECONDS = 900  # 15 min
# clave (entity_id, days) -> (ts, {variante: (medias, fiable)} | None). Las
# CUATRO variantes ("raw"/"abs"/"positive"/"negative") salen de UNA sola
# pasada sobre el mismo historico: antes cada variante pedia el historico
# entero por su cuenta (el sensor de potencia de bateria, dos veces seguidas).
_hourly_avg_cache: dict[tuple, tuple[float, dict | None]] = {}
_VARIANTS = ("raw", "abs", "positive", "negative")

# Cobertura minima (segundos de señal real) para fiarse de una franja.
MIN_COVERAGE_SECONDS_PER_HOUR = 600


def _bucket_key(ts: datetime) -> tuple[bool, int]:
    """(es_fin_de_semana, hora) -- ver `_weighted_buckets` sobre por que
    laborable y fin de semana se promedian por separado."""
    local = ts.astimezone()
    return (local.weekday() >= 5, local.hour)


def _parse_point_ts(point: dict) -> datetime | None:
    raw = point.get("last_changed") or point.get("last_updated")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _weighted_buckets(raw: list[dict], end: datetime) -> dict | None:
    """Media PONDERADA POR TIEMPO de cada cubo `(es_fin_de_semana, hora)`,
    para las cuatro variantes de signo a la vez. `None` si no hay ni una
    muestra numerica.

    Dos correcciones sobre la version anterior:

    - Ponderar por TIEMPO, no por numero de muestras. HA solo graba un estado
      cuando CAMBIA, asi que un sensor de potencia deja muchas muestras cuando
      el consumo se mueve (cocinar) y casi ninguna cuando esta quieto (la base
      de la madrugada). La media de muestras se sesgaba hacia los ratos
      movidos. Cada lectura vale hasta la siguiente -- que es justo como se
      comporta un estado en HA (y como ya integra `energy_recovery`).
    - Un estado no numerico ("unavailable"/"unknown") CORTA la lectura
      anterior y no aporta nada: ese rato no se mide, no se rellena.

    Laborable y fin de semana van en cubos distintos (48, no 24): con
    costumbres tipicas (fuera de casa entre semana, en casa el finde) una
    media mezclada no representa a ninguno de los dos.

    Glitches (ver IMPLAUSIBLE_POWER_CEILING_W): una muestra disparatada se
    descarta entera y corta la lectura anterior, igual que un "unavailable".
    """
    points: list[tuple[datetime, float | None]] = []
    for point in raw:
        ts = _parse_point_ts(point)
        if ts is None:
            continue
        try:
            val: float | None = float(point["state"])
        except (KeyError, TypeError, ValueError):
            val = None
        if val is not None and (val != val or abs(val) > IMPLAUSIBLE_POWER_CEILING_W):
            val = None
        points.append((ts, val))
    if not any(v is not None for _, v in points):
        return None
    points.sort(key=lambda x: x[0])

    keys = [(weekend, h) for weekend in (False, True) for h in range(24)]
    seconds = {k: 0.0 for k in keys}
    samples = {k: 0 for k in keys}
    sums = {v: {k: 0.0 for k in keys} for v in _VARIANTS}

    for i, (ts, val) in enumerate(points):
        if val is None:
            continue
        stop = points[i + 1][0] if i + 1 < len(points) else end
        if stop <= ts:
            # Dos estados con la misma marca (o la ultima lectura justo en el
            # borde): cuenta como muestra, sin peso de tiempo.
            samples[_bucket_key(ts)] += 1
            continue
        variants = {
            "raw": val, "abs": abs(val),
            "positive": val if val > 0 else 0.0,
            "negative": -val if val < 0 else 0.0,
        }
        samples[_bucket_key(ts)] += 1
        cursor = ts
        while cursor < stop:
            # Se parte en el cambio de hora: una lectura que dura de 13:50 a
            # 14:20 pesa 10 min en la franja de las 13 y 20 en la de las 14.
            local = cursor.astimezone()
            next_hour = (local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).astimezone(timezone.utc)
            piece_end = min(stop, next_hour)
            dur = (piece_end - cursor).total_seconds()
            if dur <= 0:
                break
            key = (local.weekday() >= 5, local.hour)
            seconds[key] += dur
            for name, v in variants.items():
                sums[name][key] += v * dur
            cursor = piece_end

    reliable = {
        k: seconds[k] >= MIN_COVERAGE_SECONDS_PER_HOUR and samples[k] >= 1 for k in keys
    }
    out = {}
    for name in _VARIANTS:
        avg: dict = {k: (sums[name][k] / seconds[k] if reliable[k] else None) for k in keys}
        # Relleno de huecos: la media de TODOS los cubos fiables (laborable +
        # finde mezclados aqui SI, a proposito -- ultimo recurso cuando una
        # franja no tiene cobertura propia).
        known = [v for v in avg.values() if v is not None]
        fallback = statistics.mean(known) if known else None
        out[name] = ({k: (avg[k] if avg[k] is not None else fallback) for k in keys}, dict(reliable))
    return out


_refreshing_keys: set[tuple] = set()
_refreshing_lock = threading.Lock()


def _refresh_hourly_avg(cache_key: tuple) -> tuple[float, dict | None]:
    entity_id, days = cache_key
    raw = _safe_get_history(entity_id, days)
    buckets = _weighted_buckets(raw, datetime.now(timezone.utc)) if raw else None
    previous = _hourly_avg_cache.get(cache_key)
    if buckets is None and previous is not None and previous[1] is not None:
        # HA no ha respondido esta vez: se conserva la media buena que ya habia
        # (se reintenta al volver a caducar) en vez de pisarla con "sin datos".
        entry = (time.time(), previous[1])
    else:
        entry = (time.time(), buckets)
    _hourly_avg_cache[cache_key] = entry
    return entry


def _refresh_hourly_avg_async(cache_key: tuple) -> None:
    with _refreshing_lock:
        if cache_key in _refreshing_keys:
            return
        _refreshing_keys.add(cache_key)

    def _run() -> None:
        try:
            _refresh_hourly_avg(cache_key)
        except Exception:
            log.warning("Fallo refrescando el historico de %s", cache_key[0], exc_info=True)
        finally:
            with _refreshing_lock:
                _refreshing_keys.discard(cache_key)

    threading.Thread(target=_run, name="ha-history-refresh", daemon=True).start()


def _hourly_avg_by_hour_of_day(
    entity_id: str, days: int, default: float, abs_values: bool, sign_filter: str | None = None
) -> tuple[dict[tuple[bool, int], float], dict[tuple[bool, int], bool]]:
    """Dos diccionarios con clave `(es_fin_de_semana, hora)`: la media y si
    esa franja tiene cobertura real suficiente. Ver `_weighted_buckets`.

    `sign_filter` ("positive" | "negative") separa un sensor bidireccional
    con signo en sus dos mitades (p.ej. la potencia de bateria: una mitad es
    carga y la otra descarga) promediando cada una como ESPERANZA -- las
    muestras del signo contrario cuentan como 0 W, no se descartan. Tiene
    prioridad sobre `abs_values`.
    """
    variant = sign_filter if sign_filter in ("positive", "negative") else ("abs" if abs_values else "raw")
    cache_key = (entity_id, days)
    cached = _hourly_avg_cache.get(cache_key)
    now_ts = time.time()
    if cached is None:
        # Sin nada todavia (arranque): no queda otra que esperar a la descarga.
        cached = _refresh_hourly_avg(cache_key)
    elif (now_ts - cached[0]) >= _HISTORY_CACHE_SECONDS:
        # Caducado pero utilizable: se sigue sirviendo lo que hay y se refresca
        # EN SEGUNDO PLANO. Descargar varios dias de un sensor que reporta cada
        # segundo tarda unos segundos; hacerlo dentro del ciclo dejaba el
        # planificador parado ese rato cada vez que caducaba la cache, para
        # unas medias de dias enteros que apenas cambian en 15 minutos.
        _refresh_hourly_avg_async(cache_key)

    buckets = cached[1]
    keys = [(weekend, h) for weekend in (False, True) for h in range(24)]
    if buckets is None:
        # Sin historico utilizable: el valor actual (o `default`) para todas
        # las franjas, marcadas como NO fiables.
        current = get_numeric_state(entity_id, default=default)
        if current is not None:
            if variant == "abs":
                current = abs(current)
            elif variant == "positive":
                current = current if current > 0 else 0.0
            elif variant == "negative":
                current = -current if current < 0 else 0.0
        return {k: current for k in keys}, {k: False for k in keys}

    avg, reliable = buckets[variant]
    if any(v is None for v in avg.values()):
        avg = {k: (v if v is not None else default) for k, v in avg.items()}
    return avg, reliable


def hourly_average_forecast_with_reliability(
    entity_id: str, horizon_hours: int, days: int = 21, default: float = 0.0, abs_values: bool = False,
    sign_filter: str | None = None,
) -> tuple[list[float], list[bool]]:
    """
    Igual que `hourly_average_forecast`, pero ademas devuelve, hora a hora,
    si ese valor viene de suficiente historico real (al menos
    MIN_COVERAGE_SECONDS_PER_HOUR de señal medida en esa franja horaria) o si es un relleno (media de las horas
    que si tienen muestra suficiente, o el valor actual si no hay historico
    en absoluto). Sirve para que quien consuma esto sepa en que horas puede
    fiarse del historico y en cuales todavia no.

    `abs_values=True` aplica valor absoluto a CADA MUESTRA antes de
    promediar (no a la media ya calculada) - imprescindible para sensores
    de potencia bidireccionales con signo (p.ej. carga positiva/descarga
    negativa en un mismo sensor): promediar primero y aplicar abs() despues
    deja que las muestras positivas y negativas de una misma franja horaria
    se CANCELEN entre si, escondiendo el verdadero movimiento de energia.

    La parte cara (pedir el historico a HA y recorrerlo) se cachea
    `_HISTORY_CACHE_SECONDS`; la alineacion al horizonte desde la hora
    ACTUAL se recalcula siempre al vuelo, nunca desde cache.
    """
    hourly_avg, reliable_by_hour = _hourly_avg_by_hour_of_day(entity_id, days, default, abs_values, sign_filter)
    now = datetime.now()
    # Alineacion por FECHA real, no solo por hora del dia -- desde que los
    # cubos distinguen laborable/fin de semana (ver _bucket_key), hace
    # falta saber que DIA CONCRETO cae cada hora del horizonte para elegir
    # el cubo correcto (p.ej. la hora 30 del horizonte puede caer en
    # sabado aunque "ahora" sea viernes).
    keys = [_bucket_key(now + timedelta(hours=i)) for i in range(horizon_hours)]
    values = [hourly_avg[k] for k in keys]
    reliable = [reliable_by_hour[k] for k in keys]
    return values, reliable


def hourly_average_forecast(
    entity_id: str, horizon_hours: int, days: int = 21, default: float = 0.0, abs_values: bool = False,
    sign_filter: str | None = None,
) -> list[float]:
    """
    Previsión simple y explicable para CUALQUIER sensor numerico: para cada
    hora del horizonte, la media de esa MISMA hora-del-dia en los ultimos
    `days` dias de historico real. Nada de aprendizaje automatico opaco.

    Si `days` supera lo que tu Home Assistant realmente conserva (por
    defecto el recorder solo guarda 10 dias), reintenta con ventanas mas
    cortas antes de rendirse - asi no depende de que sepas/ajustes ese
    detalle de configuracion tuyo.

    `sign_filter` ("positive" | "negative" | None): para sensores
    bidireccionales con signo, promedia SOLO la mitad de muestras que
    cumple el signo pedido (ver `_hourly_avg_by_hour_of_day`) — necesario
    cuando carga y descarga comparten el mismo sensor y hay que tratarlas
    por separado (ver `true_load_forecast_from_grid`).
    """
    values, _ = hourly_average_forecast_with_reliability(entity_id, horizon_hours, days, default, abs_values, sign_filter)
    return values


# alias retrocompatible
load_forecast_from_history = hourly_average_forecast


def true_load_forecast(base_consumption_sensor: str, solar_sensors: list[str],
                        horizon_hours: int, days: int = 21,
                        battery_power_sensor: str | None = None) -> list[float]:
    """
    Reconstruye el consumo REAL de la vivienda sumando el historico de cada
    componente por separado, hora a hora:

        consumo = consumo_base (red YA SIN la carga de baterias, p.ej.
                                 "consumo_instantaneo")
                + produccion_solar (de cada string/tejado declarado, sumados)
                + descarga_baterias (NO hace falta la carga: al restarse ya
                  en el sensor base, los terminos de carga se cancelan
                  matematicamente)

    `battery_power_sensor`: UN unico sensor con signo para TODO el sistema
    ("sensor.battery_orchestrator_power", positivo = descargando, negativo =
    cargando) — publicado por el propio addon, agnostico de cuantas baterias
    haya ni de que fabricante sean (HA, EcoFlow o cualquier otro). Ni la
    logica ni el calculo de esto vive en Home Assistant, solo el dato ya
    hecho; por eso no hace falta un sensor por bateria ni uno especifico
    por fabricante, solo el total que YA se publica para el dashboard.

    El signo se filtra MUESTRA A MUESTRA (no sobre la media ya calculada):
    si una franja horaria mezcla muestras de carga y descarga de distintos
    dias (p.ej. unos dias todavia cargando a esa hora, otros ya
    descargando), promediar primero y filtrar el signo despues deja que
    esas muestras se CANCELEN entre si y el resultado se hunda cerca de
    cero aunque hubiera bastante movimiento de energia real. Por eso el
    filtro se aplica antes de promediar (ver `sign_filter` en
    `hourly_average_forecast`).
    """
    total = hourly_average_forecast(base_consumption_sensor, horizon_hours, days, default=0.0)

    for ss in solar_sensors:
        if not ss:
            continue
        solar = hourly_average_forecast(ss, horizon_hours, days, default=0.0)
        total = [total[i] + solar[i] for i in range(horizon_hours)]

    if battery_power_sensor:
        discharge = hourly_average_forecast(battery_power_sensor, horizon_hours, days, default=0.0, sign_filter="positive")
        total = [total[i] + discharge[i] for i in range(horizon_hours)]

    return total


def true_load_forecast_from_grid(net_grid_sensor: str, solar_sensors: list[str],
                                  horizon_hours: int, days: int = 21,
                                  battery_power_sensor: str | None = None) -> list[float]:
    """
    Igual que `true_load_forecast`, pero para el modo "unificado" del
    sensor de consumo (ver "Consumo de la casa" en Configuración): en vez
    de un sensor que YA reste la carga de las baterías, aquí se parte del
    medidor de red EN BRUTO del punto de conexión (con signo: positivo
    importando, negativo vertiendo) — balance de potencia en el panel:

        consumo = produccion_solar + red_neta (con signo) + descarga_baterias
                  - carga_baterias

    A diferencia de `true_load_forecast`, aquí SÍ hace falta la carga por
    separado (positiva), porque el sensor de red en bruto no la excluye
    como sí hace un "consumo_instantaneo" ya neteado — sin restarla, cada
    carga se contaría dos veces como si fuera consumo de la casa.

    `battery_power_sensor`: UN unico sensor con signo para TODO el sistema
    ("sensor.battery_orchestrator_power", positivo = descargando, negativo
    = cargando), agnostico de fabricante — ver `true_load_forecast`. La
    carga y la descarga se extraen del MISMO sensor filtrando cada mitad
    por separado (`sign_filter`, ver `hourly_average_forecast`).
    """
    total = hourly_average_forecast(net_grid_sensor, horizon_hours, days, default=0.0)

    for ss in solar_sensors:
        if not ss:
            continue
        solar = hourly_average_forecast(ss, horizon_hours, days, default=0.0)
        total = [total[i] + solar[i] for i in range(horizon_hours)]

    if battery_power_sensor:
        discharge = hourly_average_forecast(battery_power_sensor, horizon_hours, days, default=0.0, sign_filter="positive")
        charge = hourly_average_forecast(battery_power_sensor, horizon_hours, days, default=0.0, sign_filter="negative")
        total = [total[i] + discharge[i] - charge[i] for i in range(horizon_hours)]

    # El consumo real nunca es negativo — un resultado negativo aqui solo
    # puede venir de ruido de medida entre sensores independientes (p.ej.
    # relojes/franjas ligeramente desalineados entre el sensor de red y el
    # de bateria), nunca de una situacion real.
    return [max(0.0, v) for v in total]


_FORECAST_TIME_KEYS = ("datetime", "date", "period_start", "start", "time")
# Solo claves en VATIOS (Solcast da `pv_estimate` en kW: no entra aqui).
_FORECAST_VALUE_KEYS = ("p_pv_forecast", "value", "power", "watts")


def _forecast_local_hour(raw) -> datetime | None:
    """Marca de tiempo de un punto de prevision, como hora local naive
    truncada a la hora (la convencion del planificador), o None."""
    if raw is None:
        return None
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    if ts.tzinfo is not None:
        ts = ts.astimezone()
    return ts.replace(tzinfo=None, minute=0, second=0, microsecond=0)


def _align_forecast_by_time(points: list[tuple[datetime, float]], horizon_hours: int) -> list[float]:
    """Serie horaria desde la hora ACTUAL a partir de puntos con marca de
    tiempo: media de los puntos de cada hora, 0 W donde no hay dato."""
    by_hour: dict[datetime, list[float]] = {}
    for ts, val in points:
        by_hour.setdefault(ts, []).append(val)
    start = datetime.now().replace(minute=0, second=0, microsecond=0)
    out = []
    for i in range(horizon_hours):
        vals = by_hour.get(start + timedelta(hours=i))
        out.append(sum(vals) / len(vals) if vals else 0.0)
    return out


def pv_forecast_from_entity(entity_id: str, horizon_hours: int) -> list[float]:
    """
    Lee la previsión solar desde un sensor de HA que exponga un atributo de
    tipo lista de pronosticos (forecast_solar, EMHASS p_pv_forecast, etc.)
    Se buscan claves de atributo habituales; si no se encuentra nada
    utilizable, se devuelve una lista de ceros (seguro, nunca inventa sol).

    BUG REAL: la serie se tomaba POR POSICION -- el primer elemento se daba por
    "la hora actual". Eso solo es cierto en las integraciones que publican
    desde ahora (EMHASS). Las que publican el dia entero (un diccionario
    `{"2026-10-02T06:00:00": 120, ...}` desde el amanecer, o una lista con
    `period_start`) quedaban desplazadas: a las 15:00 el planificador leia como
    "ahora" el valor de las 06:00. Si los puntos traen marca de tiempo, se
    alinean por ella; solo sin marca se sigue tomando por posicion.
    """
    try:
        state = get_state(entity_id)
    except (HAError, requests.RequestException):
        return [0.0] * horizon_hours

    attrs = state.get("attributes", {})
    for key in ("forecasts", "wh_hours", "watts", "forecast"):
        series = attrs.get(key)
        if not isinstance(series, (list, dict)):
            continue

        # --- con marca de tiempo: alineado por hora real -------------------
        timed: list[tuple[datetime, float]] = []
        if isinstance(series, dict):
            for k, v in series.items():
                ts = _forecast_local_hour(k)
                try:
                    if ts is not None and v is not None:
                        timed.append((ts, float(v)))
                except (TypeError, ValueError):
                    continue
        else:
            for item in series:
                if not isinstance(item, dict):
                    continue
                ts = next((t for t in (_forecast_local_hour(item.get(k)) for k in _FORECAST_TIME_KEYS) if t), None)
                val = next((item[k] for k in _FORECAST_VALUE_KEYS if item.get(k) is not None), None)
                try:
                    if ts is not None and val is not None:
                        timed.append((ts, float(val)))
                except (TypeError, ValueError):
                    continue
        if timed:
            return _align_forecast_by_time(timed, horizon_hours)

        # --- sin marca de tiempo: por posicion, desde la hora actual -------
        if isinstance(series, dict):
            values = list(series.values())[:horizon_hours]
        else:
            # Se coge la primera clave que NO sea None (un 0 -toda hora de
            # noche- es un dato valido, no una ausencia) y se conserva la
            # POSICION de cada hora: borrar las horas sin dato desplazaria
            # todas las siguientes.
            values = [
                next((item[k] for k in _FORECAST_VALUE_KEYS if isinstance(item, dict) and item.get(k) is not None), None)
                for item in series[:horizon_hours]
            ]
        # `any_real` distingue "serie con ceros de verdad" (valida) de "serie
        # con un formato que no reconocemos" (se prueba la clave siguiente).
        coerced, any_real = [], False
        for v in values:
            if v is None:
                coerced.append(0.0)
                continue
            try:
                coerced.append(float(v))
                any_real = True
            except (TypeError, ValueError):
                coerced.append(0.0)
        if any_real:
            coerced += [0.0] * (horizon_hours - len(coerced))
            return coerced[:horizon_hours]

    # sin atributo de previsión util: usar el valor actual como estimacion
    # plana solo para la proxima hora, y 0 despues (mejor infravalorar que
    # inventar produccion que no va a existir)
    try:
        current = float(state["state"])
    except (ValueError, KeyError, TypeError):
        current = 0.0
    return [current] + [0.0] * (horizon_hours - 1)
