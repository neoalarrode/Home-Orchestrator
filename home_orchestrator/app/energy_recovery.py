"""
Reconstruccion del consumo del hueco en que el addon estuvo parado.

EL PROBLEMA
-----------
`grid_energy_store.accumulate` integra usando el tiempo transcurrido desde la
ULTIMA llamada, y esa marca se persiste en disco. Al reiniciar, la primera
llamada se encuentra un "antes" de hace 40 minutos (o dos horas) y multiplica
ese hueco entero por la potencia instantanea de ESE momento, como si hubiera
sido constante todo el rato.

Su propio docstring dice que la primera llamada tras un reinicio no integra
nada. El codigo si lo hacia: la marca sobrevive al reinicio, asi que "no hay
antes" nunca se cumplia. Y el resultado no es un hueco (que se veria), sino un
numero verosimil y equivocado -- reiniciar de noche a 600 W tras un rato de
2 kW se apunta como si hubieran sido 600 W.

LA SOLUCION
-----------
Home Assistant SI estuvo grabando mientras nosotros no. Asi que el hueco no
hay que estimarlo: se lee. Se pide el historico real de los sensores de origen
para esos minutos y se integra de verdad, punto a punto.

Solo se reconstruye lo que se puede leer. Si no hay sensor de red declarado, o
HA no devuelve historico de ese rango, no se inventa nada -- se deja el hueco y
se dice en el log, que es mejor que un numero que nadie puede verificar.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("energy_recovery")

# Por debajo de esto no es un reinicio, es el ritmo normal de ciclo: no hay
# nada que reconstruir.
MIN_GAP_SECONDS = 120
# Por encima de esto no se reconstruye: HA purga su historico (10 dias por
# defecto) y, sobre todo, un hueco asi ya no es "un reinicio" sino un addon
# apagado. Se avisa y se deja constancia, en vez de rellenar a ciegas.
MAX_GAP_HOURS = 48.0


def integrate_series(points: list[dict], start: datetime, end: datetime) -> float:
    """Wh integrados de una serie de potencia (W) entre `start` y `end`.

    Integracion por rectangulos: cada lectura vale hasta la siguiente, que es
    como se comporta un sensor de estado en HA (solo publica cuando cambia).
    Los puntos anteriores a `start` no se descartan: el ultimo de ellos es el
    que dice con que potencia se ENTRA al hueco.

    Los negativos se acotan a cero: quien llama ya ha separado importacion de
    vertido, y aqui un signo raro solo restaria de un acumulado que se publica
    como `total_increasing`.
    """
    if end <= start:
        return 0.0

    muestras: list[tuple[datetime, float]] = []
    for p in points:
        ts = _parse_ts(p.get("last_updated"))
        if ts is None:
            continue
        try:
            valor = float(p.get("state"))
        except (TypeError, ValueError):
            continue  # "unavailable", "unknown"... no es una lectura
        muestras.append((ts, max(0.0, valor)))
    if not muestras:
        return 0.0
    muestras.sort(key=lambda x: x[0])

    total_wh = 0.0
    actual: float | None = None
    cursor = start
    for ts, valor in muestras:
        if ts <= start:
            actual = valor  # con esta potencia se entra al hueco
            continue
        if ts >= end:
            break
        if actual is not None:
            total_wh += actual * ((ts - cursor).total_seconds() / 3600.0)
        cursor, actual = ts, valor
    if actual is not None and cursor < end:
        total_wh += actual * ((end - cursor).total_seconds() / 3600.0)
    return total_wh


def _parse_ts(valor) -> datetime | None:
    """HA manda la marca de tiempo como epoch (float) o como ISO."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return datetime.fromtimestamp(valor, tz=timezone.utc)
    try:
        ts = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def detect_gap(last_iso: str | None, now: datetime) -> tuple[datetime, datetime] | None:
    """(inicio, fin) del hueco a reconstruir, o None si no hay que hacer nada."""
    if not last_iso:
        return None  # instalacion nueva: no hay hueco, hay ausencia de pasado
    try:
        last = datetime.fromisoformat(last_iso)
    except ValueError:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    segundos = (now - last).total_seconds()
    if segundos < MIN_GAP_SECONDS:
        return None
    if segundos > MAX_GAP_HOURS * 3600:
        log.warning(
            "El addon lleva parado %.1f h: demasiado para reconstruir el consumo con "
            "garantias (el historico de HA se purga, y un hueco asi no es un reinicio). "
            "Ese periodo se queda sin contabilizar, a proposito.", segundos / 3600.0,
        )
        return None
    return last, now


def reconstruct(ws, cfg: dict, now: datetime | None = None) -> dict | None:
    """Lee de HA lo que paso mientras estabamos parados y lo devuelve en Wh.

    Devuelve None si no habia hueco o no se pudo leer. NUNCA lanza: un fallo
    aqui no puede impedir que el addon arranque.
    """
    import grid_energy_store

    now = now or datetime.now(timezone.utc)
    try:
        hueco = detect_gap(grid_energy_store.totals().get("last_update"), now)
    except Exception:
        log.exception("Fallo mirando si hay hueco que reconstruir")
        return None
    if hueco is None:
        return None
    inicio, fin = hueco

    net_sensor = cfg.get("net_grid_sensor")
    import_sensor = cfg.get("grid_power_sensor")
    export_sensor = cfg.get("export_sensor")
    if not (net_sensor or import_sensor):
        log.info(
            "Addon parado %.0f min, pero no hay sensor de red declarado: no se puede "
            "reconstruir ese consumo sin inventarlo.", (fin - inicio).total_seconds() / 60,
        )
        return None

    start_iso = inicio.isoformat()
    try:
        if net_sensor:
            # Un medidor con signo: positivo importa, negativo vierte. Se
            # separan las dos direcciones ANTES de integrar -- integrar el neto
            # y luego partirlo daria mal las horas en que se alterna.
            puntos = ws.get_history(net_sensor, start_iso)
            importado = integrate_series(_signo(puntos, positivo=True), inicio, fin)
            vertido = integrate_series(_signo(puntos, positivo=False), inicio, fin)
        else:
            importado = integrate_series(ws.get_history(import_sensor, start_iso), inicio, fin)
            vertido = (
                integrate_series(ws.get_history(export_sensor, start_iso), inicio, fin)
                if export_sensor else 0.0
            )
    except Exception:
        log.exception("Fallo leyendo el historico de HA para reconstruir el hueco")
        return None

    minutos = (fin - inicio).total_seconds() / 60
    log.info(
        "Addon parado %.0f min: reconstruidos %.3f kWh importados y %.3f kWh vertidos "
        "desde el historico de HA (medidos, no estimados)",
        minutos, importado / 1000, vertido / 1000,
    )
    return {
        "gap_minutes": round(minutos, 1),
        "imported_wh": importado,
        "exported_wh": vertido,
        "from": start_iso,
        "to": fin.isoformat(),
    }


def run_at_startup(ws, cfg: dict, now: datetime | None = None) -> dict | None:
    """Reconstruye el hueco y lo aplica al acumulado. Se llama UNA vez al
    arrancar, antes del primer ciclo.

    Pase lo que pase se reposiciona el punto de partida: si el hueco se ha
    reconstruido, para no contarlo dos veces; y si no se ha podido, para que
    la primera integracion no lo rellene extrapolando la potencia del momento
    sobre un intervalo que nadie midio.
    """
    import grid_energy_store

    now = now or datetime.now(timezone.utc)
    try:
        datos = reconstruct(ws, cfg, now)
        if datos:
            grid_energy_store.add_energy(datos["imported_wh"], datos["exported_wh"], now)
            return datos
        grid_energy_store.reset_baseline(now)
    except Exception:
        log.exception("Fallo reconstruyendo el consumo del reinicio -- se sigue arrancando")
    return None


def _signo(puntos: list[dict], positivo: bool) -> list[dict]:
    """Un medidor con signo partido en sus dos direcciones."""
    salida = []
    for p in puntos:
        try:
            v = float(p.get("state"))
        except (TypeError, ValueError):
            continue
        valor = v if positivo else -v
        salida.append({**p, "state": max(0.0, valor)})
    return salida
