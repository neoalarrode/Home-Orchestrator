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
        self._wanted_on = {"L"}
        self._verify_timer = None
        self._lock = zr.threading.RLock()
        self.bridge = False
        for name in ("_verify_and_detect_overrides", "_apply_confirmed", "_moved_from_pre",
                     "_apply_values", "_retry_delay", "_pending_verification", "_schedule_fast_verify",
                     "_fast_verify"):
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
        return self.bridge

    def _snapshot_states(self):
        return {"L": {}}


class _FakeTimer:
    """Sustituye a threading.Timer: apunta el plazo y no arranca ningun hilo."""
    created = []

    def __init__(self, delay, fn):
        self.delay, self.fn, self.cancelled = delay, fn, False
        _FakeTimer.created.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


class LightingVerifyRetry(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self._orig = zr.time.time
        zr.time.time = self.clock
        self._orig_timer = zr.threading.Timer
        zr.threading.Timer = _FakeTimer
        _FakeTimer.created = []

    def tearDown(self):
        zr.time.time = self._orig
        zr.threading.Timer = self._orig_timer

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
        # antes del primer plazo (0,3 s): no reintenta
        self.clock.advance(zr.APPLY_SEND_FAILED_RETRY_DELAYS[0] - 0.1)
        s._verify_and_detect_overrides({}, {"L"}, retry_ids={"L"})
        self.assertEqual(len(s.sends), 1)
        # pasado el plazo: reintenta, y esta vez el envio va bien
        s.send_fails = False
        self.clock.advance(0.2)
        s._verify_and_detect_overrides({}, {"L"}, retry_ids={"L"})
        self.assertEqual(len(s.sends), 2)
        self.assertTrue(s._apply_confirmed(s._state["commanded"]["L"], s.device))

    def test_adaptive_not_reflected_is_retried(self):
        s = _Stub()
        # el envio "va bien" pero el equipo NO cambia (se queda en el previo)
        self._command(s, 80, pre={"on": True, "brightness_pct": 50, "color_temp_kelvin": 3000})
        s.device = {"on": True, "brightness_pct": 50, "color_temp_kelvin": 3000}  # no aplico
        self.clock.advance(zr.APPLY_SETTLE_SECONDS + 1)
        s._verify_and_detect_overrides({}, {"L"}, retry_ids={"L"})
        self.assertEqual(len(s.sends), 2)  # 1 original + 1 reintento

    def test_manual_change_marked_override_not_retried(self):
        s = _Stub()
        self._command(s, 80, pre={"on": True, "brightness_pct": 50, "color_temp_kelvin": 3000})
        # exito: device=80. Ahora el usuario sube a 100 a mano:
        s.device = {"on": True, "brightness_pct": 100, "color_temp_kelvin": 3000}
        self.clock.advance(zr.APPLY_SETTLE_SECONDS + 1)
        before = len(s.sends)
        s._verify_and_detect_overrides({}, {"L"}, retry_ids={"L"})
        self.assertTrue(s._state["manual_override"].get("L"))
        self.assertEqual(len(s.sends), before, "un cambio manual NO debe reintentarse (seria pelearse)")

    def test_manual_off_after_confirmed_not_retried(self):
        s = _Stub()
        self._command(s, 80, pre={"on": True, "brightness_pct": 50})
        # confirmar aplicado
        s._verify_and_detect_overrides({}, {"L"}, retry_ids={"L"})
        self.assertTrue(s._state["commanded"]["L"].get("confirmed"))
        # el usuario la apaga a mano
        s.device = {"on": False, "brightness_pct": None, "color_temp_kelvin": None}
        self.clock.advance(zr.APPLY_SETTLE_SECONDS + 1)
        before = len(s.sends)
        s._verify_and_detect_overrides({}, {"L"}, retry_ids={"L"})
        self.assertEqual(len(s.sends), before, "un apagado manual tras confirmar NO debe reintentar encender")

    def test_failed_turn_on_never_confirmed_is_retried(self):
        s = _Stub()
        s.send_fails = True
        # la luz estaba apagada; se manda encender y falla
        self._command(s, 80, pre={"on": False, "brightness_pct": None, "color_temp_kelvin": None})
        s.device = {"on": False, "brightness_pct": None, "color_temp_kelvin": None}
        self.clock.advance(zr.APPLY_SETTLE_SECONDS + 1)
        s.send_fails = False
        s._verify_and_detect_overrides({}, {"L"}, retry_ids={"L"})
        self.assertEqual(len(s.sends), 2)  # reintenta el encendido fallido

    def test_retries_bounded_then_gives_up(self):
        s = _Stub()
        s.send_fails = True
        self._command(s, 80, pre={"on": True, "brightness_pct": 50})
        s.device = {"on": True, "brightness_pct": 50, "color_temp_kelvin": None}
        for _ in range(10):
            self.clock.advance(zr.APPLY_SETTLE_SECONDS + 1)
            s._verify_and_detect_overrides({}, {"L"}, retry_ids={"L"})
        # 1 original + exactamente APPLY_MAX_RETRIES reintentos, luego se rinde
        self.assertEqual(len(s.sends), 1 + zr.APPLY_MAX_RETRIES)
        self.assertTrue(s._state["commanded"]["L"].get("gave_up"))

    def test_no_respect_manual_reasserts_instead_of_override(self):
        s = _Stub(respect_manual=False)
        self._command(s, 80, pre={"on": True, "brightness_pct": 50})
        s.device = {"on": True, "brightness_pct": 100, "color_temp_kelvin": None}  # alguien subio a 100
        self.clock.advance(zr.APPLY_SETTLE_SECONDS + 1)
        s._verify_and_detect_overrides({}, {"L"}, retry_ids={"L"})
        self.assertFalse(s._state.get("manual_override", {}).get("L"))
        self.assertEqual(len(s.sends), 2, "sin respetar manual, reafirma el valor de la zona")


class NeverTurnsOnALightTheZoneDoesNotWant(unittest.TestCase):
    """Lo que paso al desplegar la primera version: luces apagadas con una
    orden antigua guardada se reencendian solas."""

    def setUp(self):
        self.clock = _Clock()
        self._orig = zr.time.time
        zr.time.time = self.clock
        self._orig_timer = zr.threading.Timer
        zr.threading.Timer = _FakeTimer
        _FakeTimer.created = []

    def tearDown(self):
        zr.time.time = self._orig
        zr.threading.Timer = self._orig_timer

    def test_record_from_previous_version_is_never_retried(self):
        s = _Stub()
        s._state["commanded"] = {"L": {"brightness_pct": 80, "color_temp_kelvin": 3000, "ts": 10.0}}   # formato antiguo
        s.device = {"on": False, "brightness_pct": None, "color_temp_kelvin": None}
        for _ in range(5):
            self.clock.advance(zr.APPLY_SETTLE_SECONDS + 1)
            s._verify_and_detect_overrides({}, {"L"}, retry_ids={"L"})
        self.assertEqual(s.sends, [])
        self.assertFalse(s.device["on"])

    def test_light_outside_the_wanted_set_is_never_retried(self):
        s = _Stub()
        s.send_fails = True
        s.device = {"on": False, "brightness_pct": None, "color_temp_kelvin": None}
        s._apply_values("L", {"brightness_pct": 80, "color_temp_kelvin": None}, turning_on=True)
        s.send_fails = False
        for retry_ids in (set(), {"OTRA"}):        # luz natural de sobra / la regla activa ya no la incluye
            self.clock.advance(zr.APPLY_SETTLE_SECONDS + 1)
            s._verify_and_detect_overrides({}, {"L"}, retry_ids=retry_ids)
        self.clock.advance(zr.APPLY_SETTLE_SECONDS + 1)
        s._verify_and_detect_overrides({}, {"L"})                 # sin conjunto: tampoco
        self.assertEqual(len(s.sends), 1)                         # solo el envio original
        self.assertFalse(s.device["on"])

    def test_no_retry_once_the_window_has_passed(self):
        s = _Stub()
        s.send_fails = True
        s.device = {"on": False, "brightness_pct": None, "color_temp_kelvin": None}
        s._apply_values("L", {"brightness_pct": 80, "color_temp_kelvin": None}, turning_on=True)
        s.send_fails = False
        self.clock.advance(zr.APPLY_RETRY_WINDOW_SECONDS + 1)
        s._verify_and_detect_overrides({}, {"L"}, retry_ids={"L"})
        self.assertEqual(len(s.sends), 1)
        self.assertFalse(s.device["on"])

    def test_zone_turning_a_light_off_forgets_its_pending_command(self):
        s = _Stub()
        s.send_fails = True
        s.device = {"on": False, "brightness_pct": None, "color_temp_kelvin": None}
        s._apply_values("L", {"brightness_pct": 80, "color_temp_kelvin": None}, turning_on=True)
        s.send_fails = False
        calls = []

        class _WS:
            def call_service(self, domain, service, target=None):
                calls.append((domain, service, target))

        s.ws = _WS()
        zr.ZoneRunner._turn_off(s, "L")
        self.assertEqual(calls, [("light", "turn_off", {"entity_id": "L"})])
        self.assertNotIn("L", s._state["commanded"])
        self.clock.advance(zr.APPLY_SETTLE_SECONDS + 1)
        s._verify_and_detect_overrides({}, {"L"}, retry_ids={"L"})
        self.assertEqual(len(s.sends), 1)                         # no se reenciende


class RetriesAreFast(unittest.TestCase):
    """"Tiene que ser algo super rapido, que el usuario final ni lo note" -- y
    para cualquier bombilla, no solo las de una marca."""

    def setUp(self):
        self.clock = _Clock()
        self._orig = zr.time.time
        zr.time.time = self.clock
        self._orig_timer = zr.threading.Timer
        zr.threading.Timer = _FakeTimer
        _FakeTimer.created = []

    def tearDown(self):
        zr.time.time = self._orig
        zr.threading.Timer = self._orig_timer

    def _failed_adjust(self, s):
        s.send_fails = True
        s.device = {"on": True, "brightness_pct": 50, "color_temp_kelvin": None}
        s._apply_values("L", {"brightness_pct": 80, "color_temp_kelvin": None}, turning_on=False)
        s.send_fails = False

    def test_failed_send_is_resent_within_a_third_of_a_second_any_source(self):
        for bridge in (False, True):                      # HA/Matter y puente directo: igual
            s = _Stub(); s.bridge = bridge
            self._failed_adjust(s)
            self.assertAlmostEqual(_FakeTimer.created[-1].delay, 0.3, delta=0.05)   # la zona se cita sola
            self.clock.advance(0.2)
            s._fast_verify()
            self.assertEqual(len(s.sends), 1)             # aun no toca
            self.clock.advance(0.15)
            s._fast_verify()
            self.assertEqual(len(s.sends), 2)             # reenviado a los ~0,35 s
            self.assertEqual(s.device["brightness_pct"], 80)
            s._fast_verify()
            self.assertTrue(s._state["commanded"]["L"]["confirmed"])

    def test_three_failed_sends_are_spent_in_about_three_seconds(self):
        s = _Stub()
        s.send_fails = True
        s.device = {"on": True, "brightness_pct": 50, "color_temp_kelvin": None}
        s._apply_values("L", {"brightness_pct": 80, "color_temp_kelvin": None}, turning_on=False)
        t0 = self.clock.t
        for _ in range(400):
            self.clock.advance(0.05)
            s._fast_verify()
            if len(s.sends) == 1 + zr.APPLY_MAX_RETRIES:
                break
        self.assertEqual(len(s.sends), 1 + zr.APPLY_MAX_RETRIES)
        self.assertLess(self.clock.t - t0, 3.5)

    def test_accepted_but_not_applied_on_home_assistant_is_resent_under_a_second(self):
        s = _Stub()
        s._apply_values("L", {"brightness_pct": 80, "color_temp_kelvin": None}, turning_on=False)
        s.device = {"on": True, "brightness_pct": 50, "color_temp_kelvin": None}   # aceptado, pero no cambio
        s._state["commanded"]["L"].update(pre_on=True, pre_brightness_pct=50)
        self.clock.advance(0.5)
        s._fast_verify()
        self.assertEqual(len(s.sends), 1)
        self.clock.advance(0.25)
        s._fast_verify()
        self.assertEqual(len(s.sends), 2)
        self.assertEqual(s.device["brightness_pct"], 80)

    def test_bridge_is_not_resent_blindly_before_it_can_report(self):
        """Un puente que tarda segundos en reflejar el estado: reenviar antes solo
        duplica una orden que ya se aplico."""
        s = _Stub(); s.bridge = True
        s._apply_values("tplink:abc", {"brightness_pct": 80, "color_temp_kelvin": None}, turning_on=False)
        s._wanted_on = {"tplink:abc"}
        s.device = {"on": True, "brightness_pct": 50, "color_temp_kelvin": None}   # el sondeo aun no ha pasado
        s._state["commanded"]["tplink:abc"].update(pre_on=True, pre_brightness_pct=50)
        self.clock.advance(3.0)
        s._fast_verify()
        self.assertEqual(len(s.sends), 1)
        self.clock.advance(3.1)
        s._fast_verify()
        self.assertEqual(len(s.sends), 2)

    def test_nothing_is_scheduled_once_confirmed_or_not_wanted(self):
        s = _Stub()
        s._apply_values("L", {"brightness_pct": 80, "color_temp_kelvin": None}, turning_on=False)   # se aplica
        self.clock.advance(0.8)
        s._fast_verify()
        n = len(_FakeTimer.created)
        s._schedule_fast_verify()
        self.assertEqual(len(_FakeTimer.created), n)       # confirmada: sin mas citas
        s2 = _Stub()
        self._failed_adjust(s2)
        s2._wanted_on = set()                               # la zona ya no la quiere encendida
        self.clock.advance(1.0)
        s2._fast_verify()
        self.assertEqual(len(s2.sends), 1)

    def test_unreadable_home_assistant_state_concludes_nothing(self):
        s = _Stub()
        s._apply_values("L", {"brightness_pct": 80, "color_temp_kelvin": None}, turning_on=False)
        s.device = {"on": False, "brightness_pct": None, "color_temp_kelvin": None}
        s._snapshot_states = lambda: {}                     # lectura vacia (hipo del WebSocket)
        self.clock.advance(1.0)
        s._fast_verify()
        self.assertEqual(len(s.sends), 1)


if __name__ == "__main__":
    unittest.main()
