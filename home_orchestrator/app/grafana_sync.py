"""
Autoconfigurador del dashboard de Grafana ("Energía — Centro de Control") --
mantiene sincronizados con la config real los pocos elementos del dashboard
que SI dependen de ella, en vez de tener que editarlos a mano en Grafana
cada vez que se añade/quita un array solar.

Deliberadamente NO toca el datasource de Grafana (VictoriaMetrics, uid fijo
mas abajo): ya existe, funciona, y lleva credenciales Basic Auth propias --
recrearlo/tocarlo automaticamente es un riesgo real de romper el acceso a
Grafana para ganar poco, as que se limita a comprobar que sigue existiendo
y avisar si no.

Que SI se regenera en cada sincronizacion (manual, desde el boton de la
interfaz, o automatica al guardar cambios en los arrays solares):

  - El panel "Generación solar por panel/array declarado": antes tenia el
    id de un array concreto QUEMADO en su query (`sensor.battery_
    orchestrator_solar_ea12a052`) -- si se añadia o se quitaba un array,
    el panel se quedaba desfasado sin que nadie lo notara hasta mirarlo.
    Ahora se reconstruye su lista de queries a partir de `cfg["pv_arrays"]`
    en cada sincronizacion.
  - El panel "Previsión solar hoy / mañana": consultaba dos sensores de
    OTRA integracion de HA (`sensor.estimacion_solar_total_hoy/manana`),
    ajenos a este plugin -- a peticion expresa del usuario, se reescribe
    para consultar los sensores propios que este mismo plugin publica
    (`sensor.battery_orchestrator_solar_forecast_today/tomorrow`, ver
    main.py:run_cycle) en vez de depender de una fuente externa.

Requiere una API key con rol Editor de una service account de Grafana
(Administration -> Users and access -> Service accounts) y la URL desde la
que el propio addon (network_mode: host) puede alcanzar Grafana -- NUNCA el
puerto "direct access" (8080 en esta instalacion): ese pasa por el nginx
del addon de Grafana, que para peticiones API con `Authorization: Bearer`
devuelve 302/reset (mismo nginx que ya daba problemas de CSRF con sesiones
de navegador). Hay que apuntar directo al puerto propio de Grafana (3000
dentro de su contenedor) por la IP/nombre de ese contenedor en la red
`hassio` del Supervisor -- verificado en real contra la instalacion del
usuario.
"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger("grafana_sync")

REQUEST_TIMEOUT_SECONDS = 10

DASHBOARD_UID = "energia-mission-control"
DATASOURCE_UID = "bfwskhk4o8k5cf"

ARRAY_PANEL_TITLE = "Generación solar por panel/array declarado"
SOLAR_FORECAST_PANEL_TITLE = "Previsión solar hoy / mañana"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _rebuild_array_panel_targets(panel: dict, pv_arrays: list[dict]) -> None:
    targets = [
        {
            "expr": f'hass_sensor_power_w{{entity="sensor.battery_orchestrator_solar_{array["id"]}"}}',
            "legendFormat": array.get("name") or array["id"],
        }
        for array in pv_arrays
    ]
    # El agregado SIEMPRE va el ultimo, tal cual estaba en el panel original
    # -- referencia visual constante mientras el resto de series cambian.
    targets.append({
        "expr": 'hass_sensor_power_w{entity="sensor.battery_orchestrator_solar_power"}',
        "legendFormat": "Total agregado",
    })
    panel["targets"] = targets


def _fix_solar_forecast_panel(panel: dict) -> None:
    panel["targets"] = [
        {
            "expr": 'hass_sensor_energy_kwh{entity="sensor.battery_orchestrator_solar_forecast_today"}',
            "legendFormat": "Resto de hoy",
        },
        {
            "expr": 'hass_sensor_energy_kwh{entity="sensor.battery_orchestrator_solar_forecast_tomorrow"}',
            "legendFormat": "Mañana",
        },
    ]


def _find_panel(panels: list[dict], title: str) -> dict | None:
    for panel in panels:
        if panel.get("title") == title:
            return panel
    return None


def check_datasource(grafana_url: str, grafana_token: str) -> dict:
    """Solo LECTURA -- ver docstring del modulo, nunca se crea/modifica el
    datasource desde aqui. Devuelve {"ok": True} si existe y responde, o
    {"ok": False, "error": ...} explicando que revisar a mano si no."""
    try:
        r = requests.get(
            f"{grafana_url}/api/datasources/uid/{DATASOURCE_UID}",
            headers=_headers(grafana_token), timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        return {"ok": False, "error": f"No se pudo contactar con Grafana: {e}"}
    if r.status_code == 404:
        return {
            "ok": False,
            "error": f"El datasource de VictoriaMetrics (uid {DATASOURCE_UID}) no existe en ese Grafana "
                     "-- revísalo a mano, este plugin nunca lo crea automáticamente.",
        }
    if not r.ok:
        return {"ok": False, "error": f"Grafana respondió {r.status_code} comprobando el datasource."}
    return {"ok": True}


def sync(grafana_url: str, grafana_token: str, pv_arrays: list[dict]) -> dict:
    """Punto de entrada unico -- llamado tanto desde el boton manual de la
    interfaz como automaticamente al guardar cambios en los arrays solares
    (ver main.py). Nunca lanza excepcion: todo fallo vuelve como
    {"ok": False, "error": ...} para que el llamante decida si lo muestra
    o solo lo registra en el log (la sincronizacion automatica no debe
    tumbar el guardado de la config si Grafana esta caido, por ejemplo)."""
    if not grafana_url or not grafana_token:
        return {"ok": False, "error": "Grafana no está configurado (falta URL o token)."}

    ds_check = check_datasource(grafana_url, grafana_token)
    if not ds_check["ok"]:
        return ds_check

    try:
        r = requests.get(
            f"{grafana_url}/api/dashboards/uid/{DASHBOARD_UID}",
            headers=_headers(grafana_token), timeout=REQUEST_TIMEOUT_SECONDS,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        return {"ok": False, "error": f"No se pudo leer el dashboard de Grafana: {e}"}

    dashboard = r.json()["dashboard"]
    panels = dashboard.get("panels", [])

    array_panel = _find_panel(panels, ARRAY_PANEL_TITLE)
    if array_panel is not None:
        _rebuild_array_panel_targets(array_panel, pv_arrays)

    forecast_panel = _find_panel(panels, SOLAR_FORECAST_PANEL_TITLE)
    if forecast_panel is not None:
        _fix_solar_forecast_panel(forecast_panel)

    payload = {
        "dashboard": dashboard,
        "overwrite": True,
        "message": "Sincronizado automáticamente por Home Orchestrator (Energy)",
    }
    try:
        r = requests.post(
            f"{grafana_url}/api/dashboards/db",
            headers=_headers(grafana_token), json=payload, timeout=REQUEST_TIMEOUT_SECONDS,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        return {"ok": False, "error": f"No se pudo subir el dashboard actualizado a Grafana: {e}"}

    return {"ok": True, "message": "Dashboard de Grafana sincronizado."}
