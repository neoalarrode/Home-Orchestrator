"""Shim de compatibilidad. El plugin Tuya es AHORA el nativo (reimplementa la
app Tuya: firma HMAC con K, control local+remoto, entidades HA nativas por MQTT
y consumo interno via device_registry). Ver tuya_native_plugin.py.

El cargador hace `from tuya_plugin import TuyaPlugin` (plugin_loader._tuya);
esta linea resuelve ese nombre al plugin nuevo sin tocar el loader ni exigir un
release del core. La implementacion antigua (clase TuyaPlugin sobre tuya/ y
tuya_templates/) queda en el historial de git para rollback.
"""
from tuya_native_plugin import TuyaNativePlugin as TuyaPlugin  # noqa: F401
