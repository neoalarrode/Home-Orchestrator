"""El hilo de reconexion de Shelly sobrevive a una respuesta inesperada de un
equipo (antes solo atrapaba errores de red y moria: ningun Shelly caido volvia)."""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import shelly.device_manager as dm  # noqa: E402


class _Stop(Exception):
    pass


class ReconnectSurvives(unittest.TestCase):
    def test_bad_reply_does_not_kill_the_loop(self):
        m = dm.ShellyDeviceManager()
        m._devices = {"a": {"host": "1.1.1.1", "gen": None}, "b": {"host": "2.2.2.2", "gen": None}}
        calls = []

        def detect(host):
            calls.append(host)
            if host == "1.1.1.1":
                raise ValueError("no es JSON")
            return {"gen": 2, "capability": "switch", "model": "x"}

        sleeps = iter([None, _Stop()])

        def fake_sleep(_):
            v = next(sleeps)
            if isinstance(v, Exception):
                raise v

        with mock.patch.object(m, "_detect", side_effect=detect), \
             mock.patch.object(m, "_refresh", side_effect=TypeError("lights")), \
             mock.patch.object(dm.time, "sleep", side_effect=fake_sleep):
            with self.assertRaises(_Stop):
                m._reconnect_loop()
        self.assertEqual(calls, ["1.1.1.1", "2.2.2.2"])     # el segundo se intento igual
        self.assertEqual(m._devices["b"]["gen"], 2)


if __name__ == "__main__":
    unittest.main()
