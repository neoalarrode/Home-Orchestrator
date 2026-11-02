"""Un humidificador (o extractor) desconectado no recibe ordenes en cada ciclo (2-nov: ~4
turn_off por minuto a uno 'unavailable'); al volver se le manda con normalidad."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402


class _WS:
    def __init__(self): self.calls = []
    def call_service(self, domain, service, **k): self.calls.append((domain, service))


class _Stub:
    def __init__(self, state):
        self.zone = {zr.CONF_SIMULATE: False, zr.CONF_HUMIDIFIER_ENTITIES: ["humidifier.h"]}
        self.ws = _WS(); self.state = state; self.target_humidity = 50
        self.drive = zr.ZoneRunner._drive_humidifiers.__get__(self)
        self._ha_call = zr.ZoneRunner._ha_call.__get__(self)
    def _get_state(self, e): return self.state


def _st(s, humidity=40):
    return {"state": s, "attributes": {"humidity": humidity}}


class HumidifierUnavailable(unittest.TestCase):
    def test_unavailable_gets_nothing_when_idle(self):
        s = _Stub(_st("unavailable")); s.drive(False)
        self.assertEqual(s.ws.calls, [])

    def test_unavailable_gets_nothing_when_active(self):
        s = _Stub(_st("unavailable")); s.drive(True)
        self.assertEqual(s.ws.calls, [])

    def test_on_is_turned_off_when_idle(self):
        s = _Stub(_st("on")); s.drive(False)
        self.assertEqual(s.ws.calls, [("humidifier", "turn_off")])

    def test_off_is_turned_on_and_set_when_active(self):
        s = _Stub(_st("off")); s.drive(True)
        self.assertEqual(s.ws.calls, [("humidifier", "turn_on"), ("humidifier", "set_humidity")])

    def test_off_and_idle_gets_nothing(self):
        s = _Stub(_st("off")); s.drive(False)
        self.assertEqual(s.ws.calls, [])


class ExtractorUnavailable(unittest.TestCase):
    def _stub(self, state):
        s = _Stub(state)
        s.zone = {zr.CONF_SIMULATE: False, zr.CONF_EXTRACTOR_SWITCHES: ["switch.x"], zr.CONF_EXTRACTOR_FANS: ["fan.x"]}
        s.drive = zr.ZoneRunner._drive_extractor.__get__(s)
        return s

    def test_unavailable_gets_nothing(self):
        for active in (False, True):
            s = self._stub(_st("unavailable")); s.drive(active)
            self.assertEqual(s.ws.calls, [])

    def test_on_is_turned_off(self):
        s = self._stub(_st("on")); s.drive(False)
        self.assertEqual(s.ws.calls, [("switch", "turn_off"), ("fan", "turn_off")])


if __name__ == "__main__":
    unittest.main()
