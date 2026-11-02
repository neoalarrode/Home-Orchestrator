"""Una orden que falla (aire que la nube de Tuya da por desconectado, o un error
de HA) no aborta el ciclo de la zona: queda anotada y se reintenta con la
espera de confirmacion de siempre (2-nov, 11:56: DEVICE_OFFLINE en Salon y
Cuarto de juegos tumbaba el ciclo entero)."""
import os, sys, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402

T0 = datetime(2026, 11, 2, 11, 56, tzinfo=timezone.utc)


class ApiError(Exception):
    pass


class _Handle:
    available = True
    def __init__(self): self.calls = 0
    def set_temperature(self, t):
        self.calls += 1
        raise ApiError("DEVICE_OFFLINE: Device offline")


class _Bridges:
    def __init__(self, handle): self.handle = handle
    def is_bridge_ref(self, e): return e.startswith("tuya:")
    def resolve_bridge_handle(self, e): return self.handle


class _WS:
    def __init__(self): self.calls = 0
    def call_service(self, *a, **k):
        self.calls += 1
        raise RuntimeError("HA: entidad no encontrada")


class _Stub:
    def __init__(self, handle=None):
        self.zone = {"name": "Salon"}
        self.bridges = _Bridges(handle)
        self.ws = _WS()
        self._delegate_last_command = {}
        for name in ("_send_delegate_once", "_note_delegate_command", "_call_climate_service",
                     "_is_bridge_ref", "_resolve_bridge_handle"):
            setattr(self, name, getattr(zr.ZoneRunner, name).__get__(self))


class SendFailure(unittest.TestCase):
    def test_offline_bridge_device_does_not_raise_and_retries_later(self):
        h = _Handle()
        s = _Stub(h)
        with self.assertLogs("climate.zone_runner", "WARNING"):
            self.assertTrue(s._send_delegate_once("tuya:ac", "temperature", 21.0, "set_temperature",
                                                  {"temperature": 21.0}, T0))
        # Mismo valor enseguida: se espera a la confirmacion, no se martillea la nube.
        self.assertFalse(s._send_delegate_once("tuya:ac", "temperature", 21.0, "set_temperature",
                                               {"temperature": 21.0}, T0 + timedelta(seconds=30)))
        self.assertEqual(h.calls, 1)
        later = T0 + timedelta(seconds=zr.DELEGATE_CONFIRM_WAIT_SECONDS + 1)
        with self.assertLogs("climate.zone_runner", "WARNING"):
            s._send_delegate_once("tuya:ac", "temperature", 21.0, "set_temperature", {"temperature": 21.0}, later)
        self.assertEqual(h.calls, 2)

    def test_ha_service_error_does_not_raise(self):
        s = _Stub()
        with self.assertLogs("climate.zone_runner", "WARNING"):
            s._call_climate_service("climate.salon", "set_temperature", {"temperature": 21})
        self.assertEqual(s.ws.calls, 1)


if __name__ == "__main__":
    unittest.main()
