"""12-nov: abrir un puerto pide el nombre de la maquina y en la imagen del
add-on esa consulta falla a ratos (UnicodeDecodeError). En el puerto 8099
tumbaria el add-on entero."""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import core_app  # noqa: E402

GARBAGE = UnicodeDecodeError("utf-8", b"\xb8", 0, 1, "invalid start byte")


class CoreServeRetry(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(core_app.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def test_retries_until_the_port_opens(self):
        with mock.patch.object(core_app, "run_simple", side_effect=[GARBAGE, GARBAGE, None]) as run:
            core_app._serve("app")
        self.assertEqual(run.call_count, 3)
        run.assert_called_with("0.0.0.0", 8099, "app", threaded=True)

    def test_real_run_simple_survives_a_failed_lookup(self):
        import werkzeug.serving
        real = werkzeug.serving.run_simple
        served = []

        def serve_forever(server, *args, **kwargs):
            served.append(server.socket.getsockname()[1])
            server.server_close()

        with mock.patch("socket.getfqdn", side_effect=[GARBAGE, "equipo"]) as lookup, \
                mock.patch("werkzeug.serving.BaseWSGIServer.serve_forever", serve_forever), \
                mock.patch.object(core_app, "run_simple",
                                  side_effect=lambda host, port, app, **kw: real("127.0.0.1", 0, app, **kw)):
            core_app._serve(lambda environ, start_response: [])
        self.assertEqual(lookup.call_count, 2)
        self.assertEqual(len(served), 1)

    def test_gives_up_after_the_last_attempt(self):
        with mock.patch.object(core_app, "run_simple", side_effect=GARBAGE) as run:
            with self.assertRaises(UnicodeDecodeError):
                core_app._serve("app")
        self.assertEqual(run.call_count, core_app.SERVER_START_ATTEMPTS)

    def test_other_errors_are_not_retried(self):
        with mock.patch.object(core_app, "run_simple", side_effect=OSError("ocupado")) as run:
            with self.assertRaises(OSError):
                core_app._serve("app")
        self.assertEqual(run.call_count, 1)


class SafeGetfqdn(unittest.TestCase):
    def test_installed_process_wide(self):
        import socket
        self.assertIs(socket.getfqdn, core_app._safe_getfqdn)

    def test_unreadable_name_falls_back_to_the_name(self):
        with mock.patch.object(core_app, "_getfqdn", side_effect=GARBAGE):
            self.assertEqual(core_app._safe_getfqdn("0.0.0.0"), "0.0.0.0")
            with mock.patch("socket.gethostname", return_value="equipo"):
                self.assertEqual(core_app._safe_getfqdn(), "equipo")

    def test_normal_lookups_are_untouched(self):
        with mock.patch.object(core_app, "_getfqdn", return_value="host.local") as real:
            self.assertEqual(core_app._safe_getfqdn("1.2.3.4"), "host.local")
        real.assert_called_once_with("1.2.3.4")

    def test_a_server_opens_while_every_lookup_is_unreadable(self):
        from werkzeug.serving import make_server
        with mock.patch.object(core_app, "_getfqdn", side_effect=GARBAGE):
            server = make_server("127.0.0.1", 0, lambda e, s: [], threaded=True)
        server.server_close()
        self.assertEqual(server.server_name, "127.0.0.1")
