"""
Contador de por vida de la energia que sale por cada enchufe Schuko de las
baterias EcoFlow (peticion del usuario, 5-nov: lo enchufado ahi no lo ve el
Shelly de la casa mientras tira de bateria, y sin estos sensores la auditoria
de consumo no puede repartirlo). Mismo mecanismo que solar_energy_store.py,
una entrada por enchufe: {"<clave>": {"since": iso, "wh": float}}. Tambien
guarda el acumulado de "Aires (estimado)" (clave "aires", ver main.py).
"""

from __future__ import annotations

import os
import threading
from datetime import datetime

import json_store

OUTLET_ENERGY_PATH = os.environ.get("OUTLET_ENERGY_PATH", "/data/outlet_energy.json")

_lock = threading.RLock()


def _load() -> dict:
    data = json_store.load(OUTLET_ENERGY_PATH)
    return data if isinstance(data, dict) else {}


def accumulate(key: str, wh: float) -> dict:
    """Suma `wh` (>= 0) al enchufe `key` y devuelve su {"since", "wh"}."""
    with _lock:
        data = _load()
        entry = data.get(key) if isinstance(data.get(key), dict) else {"since": None, "wh": 0.0}
        if wh > 0:
            if entry.get("since") is None:
                entry["since"] = datetime.now().isoformat()
            entry["wh"] = float(entry.get("wh") or 0.0) + wh
            data[key] = entry
            json_store.save(OUTLET_ENERGY_PATH, data)
        return dict(entry)
