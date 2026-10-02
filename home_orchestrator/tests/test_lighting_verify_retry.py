"""Verificacion + reintento del apply y override manual en Lighting
(stdlib unittest): python -m unittest discover -s home_orchestrator/tests

Dos bugs reales confirmados en produccion:
  1. Un `light.turn_on` que falla (excepcion de red / el equipo no aplica la
     orden) no se reintentaba -> la bombilla no cambiaba y nadie insistia.
  2. Un cambio manual (subir el brillo al 100%) se re-adaptaba al rato -> no se
     marcaba de forma fiable como "tocado a mano".
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import lighting.zone_runner as zr  # noqa: E402


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class _Stub:
    """ZoneRunner minimo con los metodos de verificacion reales enganchados.
    `device` es el estado REAL simulado de la unica luz ('L'); `sends` registra
    los envios; `send_fails` hace que el envio lance (como el timeout KLAP)."""
    def __init__(self, respect_manual=True):
        self.zone_id = "z"
        self.zone = {"respect_manual_changes": respect_manual}
        self._state = {}
        self.device = {"on": False, "brightness_pct": None, "color_temp_kelvin": None}
        self.sends = []
        self.send_fails = False
        self._last_states = {}
        for name in ("_verify_and_detect_overrides", "_apply_confirmed", "_moved_from_pre",
                     "_apply_values"):
            setattr(self, name, getattr(zr.ZoneRunner, name).__get__(self))
        # `_within` es @staticmethod: se usa tal cual, sin enlazar a self
        # (enlazarlo lo convertiria en metodo y le colaria `self` como 1er arg).
        self._within = zr.ZoneRunner._within

    def _current_light_values(self, states, entity_id):
        return dict(self.device)

    def _send_to_light(self, entity_id, brightness_pct, color_temp_kelvin, hs):
        self.sends.append((brightness_pct, color_temp_kelvin))
        if self.send_fails:
            return False
        # exito: el "dispositivo" aplica lo mandado
        self.device["on"] = True
        if brightness_pct is not None:
            self.device["brightness_pct"] = brightness_pct
        if color_temp_kelvin is not None:
            self.device["color_temp_kelvin"] = color_temp_kelvin
        return True

    def _is_bridge_ref(self, e):
        return False


class LightingVerifyRetry(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self._orig = zr.time.time
        zr.time.time = self.clock

    def tearDown(self):
        zr.time.time = self._orig

    def _command(self, s, brightness, color=None, pre=None):
        """Simula que la zona manda un cambio (via _apply_values real)."""
        s.device = dict(pre) if pre else {"on": True, "brightness_pct": 50, "color_temp_kelvin": 3000}
        s._apply_values("L", {"brightness_pct": brightness, "color_temp_kelvin": color}, turning_on=False)

    def test_failed_send_is_retried_until_applied(self):
        s = _Stub()
        s.send_fails = True
        self._command(s, 80, 3500)  # envio falla
        self.assertEqual(s._state["commanded"]["L"]["attempts"], 0)  # intento fresco
        # el dispositivo sigue en su valor previo
        self.assertEqual(s.device["brightness_pct"], 50)
        # antes de la ventana de asentamiento: no reintenta
        self.clock.advance(zr.APPLY_SETTLE_SECONDS - 1)
        s._verify_and_detect_overrides({}, {"L"})
        self.assertEqual(len(s.sends), 1)
        # pasada la ventana: reintenta, y esta vez el envio va bien
        s.send_fails = False
        self.clock.advance(2)
        s._verify_and_detect_overrides({}, {"L"})
        self.assertEqual(len(s.sends), 2)
        self.assertTrue(s._apply_confirmed(s._state["commanded"]["L"], s.device))

    def test_adaptive_not_reflected_is_retried(self):
        s = _Stub()
        # el envio "va bien" pero el equipo NO cambia (se queda en el previo)
        self._command(s, 80, pre={"on": True, "brightness_pct": 50, "color_temp_kelvin": 3000})
        s.device = {"on": True, "brightness_pct": 50, "color_temp_kelvin": 3000}  # no aplico
        self.clock.advance(zr.APPLY_SETTLE_SECONDS + 1)
        s._verify_and_detect_overrides({}, {"L"})
        self.assertEqual(len(s.sends), 2)  # 1 original + 1 reintento

    def test_manual_change_marked_override_not_retried(self):
        s = _Stub()
        self._command(s, 80, pre={"on": True, "brightness_pct": 50, "color_temp_kelvin": 3000})
        # exito: device=80. Ahora el usuario sube a 100 a mano:
        s.device = {"on": True, "brightness_pct": 100, "color_temp_kelvin": 3000}
        self.clock.advance(zr.APPLY_SETTLE_SECONDS + 1)
        before = len(s.sends)
        s._verify_and_detect_overrides({}, {"L"})
        self.assertTrue(s._state["manual_override"].get("L"))
        self.assertEqual(len(s.sends), before, "un cambio manual NO debe reintentarse (seria pelearse)")

    def test_manual_off_after_confirmed_not_retried(self):
        s = _Stub()
        self._command(s, 80, pre={"on": True, "brightness_pct": 50})
        # confirmar aplicado
        s._verify_and_detect_overrides({}, {"L"})
        self.assertTrue(s._state["commanded"]["L"].get("confirmed"))
        # el usuario la apaga a mano
        s.device = {"on": False, "brightness_pct": None, "color_temp_kelvin": None}
        self.clock.advance(zr.APPLY_SETTLE_SECONDS + 1)
        before = len(s.sends)
        s._verify_and_detect_overrides({}, {"L"})
        self.assertEqual(len(s.sends), before, "un apagado manual tras confirmar NO debe reintentar encender")

    def test_failed_turn_on_never_confirmed_is_retried(self):
        s = _Stub()
        s.send_fails = True
        # la luz estaba apagada; se manda encender y falla
        self._command(s, 80, pre={"on": False, "brightness_pct": None, "color_temp_kelvin": None})
        s.device = {"on": False, "brightness_pct": None, "color_temp_kelvin": None}
        self.clock.advance(zr.APPLY_SETTLE_SECONDS + 1)
        s.send_fails = False
        s._verify_and_detect_overrides({}, {"L"})
        self.assertEqual(len(s.sends), 2)  # reintenta el encendido fallido

    def test_retries_bounded_then_gives_up(self):
        s = _Stub()
        s.send_fails = True
        self._command(s, 80, pre={"on": True, "brightness_pct": 50})
        s.device = {"on": True, "brightness_pct": 50, "color_temp_kelvin": None}
        for _ in range(10):
            self.clock.advance(zr.APPLY_SETTLE_SECONDS + 1)
            s._verify_and_detect_overrides({}, {"L"})
        # 1 original + exactamente APPLY_MAX_RETRIES reintentos, luego se rinde
        self.assertEqual(len(s.sends), 1 + zr.APPLY_MAX_RETRIES)
        self.assertTrue(s._state["commanded"]["L"].get("gave_up"))

    def test_no_respect_manual_reasserts_instead_of_override(self):
        s = _Stub(respect_manual=False)
        self._command(s, 80, pre={"on": True, "brightness_pct": 50})
        s.device = {"on": True, "brightness_pct": 100, "color_temp_kelvin": None}  # alguien subio a 100
        self.clock.advance(zr.APPLY_SETTLE_SECONDS + 1)
        s._verify_and_detect_overrides({}, {"L"})
        self.assertFalse(s._state.get("manual_override", {}).get("L"))
        self.assertEqual(len(s.sends), 2, "sin respetar manual, reafirma el valor de la zona")


if __name__ == "__main__":
    unittest.main()
