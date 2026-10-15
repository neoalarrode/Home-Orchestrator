"""El arranque de TP-Link conecta con los dispositivos EN PARALELO, no en serie.

Regresion de 2026-10-15: con 14 dispositivos (varios inalcanzables), el bucle
serial de `start_background_threads` bloqueaba ~210 s (15 s por equipo), y durante
ese rato el ingress del addon no respondia. Debe tardar lo que el mas lento, no la
suma.
"""
import os
import sys
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

# `kasa` no esta instalado en el entorno de test y no se usa en esta prueba
# (se sustituye `_start_device` entero), basta con un stub para poder importar.
if "kasa" not in sys.modules:
    _kasa = types.ModuleType("kasa")
    for _n in ("Credentials", "Discover", "Device", "Module",
               "AuthenticationError", "KasaException"):
        setattr(_kasa, _n, type(_n, (Exception,) if _n.endswith(("Error", "Exception")) else (object,), {}))
    sys.modules["kasa"] = _kasa


class TplinkStartupIsParallel(unittest.TestCase):
    def _make_plugin(self):
        import tplink_plugin
        plugin = tplink_plugin.TplinkPlugin.__new__(tplink_plugin.TplinkPlugin)
        # Solo lo que toca start_background_threads; nada de red real.
        plugin._manager = types.SimpleNamespace(start=lambda: None)
        plugin._mqtt = types.SimpleNamespace(connect=lambda: None)
        plugin._stop_rediscover = __import__("threading").Event()
        return plugin, tplink_plugin

    def test_devices_start_concurrently(self):
        plugin, tplink_plugin = self._make_plugin()
        n = 12
        devices = [{"id": "d%d" % i, "config": {"host": "10.0.0.%d" % i}} for i in range(n)]
        delay = 0.3  # simula un equipo que tarda (timeout de conexion)

        started = []

        def fake_start_device(device):
            started.append(device["id"])
            time.sleep(delay)

        with mock.patch.object(tplink_plugin, "tplink_store") as store, \
                mock.patch.object(plugin, "_start_device", side_effect=fake_start_device), \
                mock.patch.object(plugin, "_rediscover_loop", lambda *a, **k: None):
            store.load_devices.return_value = devices
            t0 = time.monotonic()
            plugin.start_background_threads()
            elapsed = time.monotonic() - t0

        self.assertEqual(sorted(started), sorted(d["id"] for d in devices))
        # En serie serian n*delay (3.6 s). En paralelo, ~delay. Margen amplio
        # para no depender de la maquina: basta con que sea MUCHO menos que serie.
        self.assertLess(elapsed, n * delay / 2,
                        "el arranque de TP-Link sigue siendo en serie (%.2fs)" % elapsed)


if __name__ == "__main__":
    unittest.main()
