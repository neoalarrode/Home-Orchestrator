"""Migra plugins.tuya (antiguo) -> plugins.tuya_native. Conserva por dispositivo
name/device_id/local_key/address/protocol_version/expose_mqtt; descarta el
profile_yaml a mano (perfil dinamico por product_id); auth pasa a app_password/qr."""
from __future__ import annotations
from typing import Any, Mapping, Optional
def migrate_devices(legacy, app_devices=None):
    app_devices=app_devices or {}; out=[]
    for d in (legacy.get("devices") or []):
        cfg=d.get("config") or {}; did=cfg.get("device_id") or ""; ex=app_devices.get(did,{})
        out.append({"id":d.get("id"),"name":cfg.get("name") or "","device_id":did,
            "local_key":cfg.get("local_key") or "","address":cfg.get("address") or "",
            "protocol_version":cfg.get("protocol_version") or "3.3","expose_mqtt":bool(cfg.get("expose_mqtt")),
            "category":ex.get("category") or cfg.get("category") or "",
            "product_id":ex.get("product_id") or cfg.get("product_id") or "","profile_source":"dynamic"})
    return out
def migrate_section(legacy, app_session=None, app_devices=None):
    acc=legacy.get("account") or {}
    return {"devices":migrate_devices(legacy,app_devices),
        "auth":{"mode":"app_password" if (app_session or {}).get("email") else "app_qr",
                "email":(app_session or {}).get("email") or "",
                "country_code":(app_session or {}).get("country_code") or "34",
                "uid":(app_session or {}).get("uid") or acc.get("uid") or "","region":acc.get("region") or ""},
        "migrated_from":"tuya"}
