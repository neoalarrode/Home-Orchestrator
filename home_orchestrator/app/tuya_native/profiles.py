"""Perfil normalizado de un producto desde su thing-model: code<->dpId, tipo, spec."""
from __future__ import annotations
from typing import Any, Dict

def parse_thing_model(tm: dict) -> Dict[str, dict]:
    codes: Dict[str, dict] = {}
    def walk(o):
        if isinstance(o, dict):
            if "code" in o and ("dpId" in o or "abilityId" in o):
                sp = o.get("typeSpec") or {}
                codes[o["code"]] = {"dp": o.get("dpId") or o.get("abilityId"),
                    "type": sp.get("type") or o.get("type"),
                    "spec": {**sp, "accessMode": o.get("accessMode"), "extensions": o.get("extensions")}}
            for v in o.values(): walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    walk(tm)
    return codes
