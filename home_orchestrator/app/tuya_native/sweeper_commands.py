"""Comandos del SweeperKit (aspirador). roomCleanSet via MQTT proto64 + arranque
(mode=select_room, switch_go). VERIFICADO en vivo (el robot limpio el Salon)."""
from __future__ import annotations
from . import mqtt_transport
def room_clean(room_ids, **kw): return mqtt_transport.room_clean_message(room_ids, **kw)
START_SEQUENCE = [("roomCleanSet_mqtt", 64), ("dp:mode", "select_room"), ("dp:switch_go", True)]
