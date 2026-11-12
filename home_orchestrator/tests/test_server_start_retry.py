"""12-nov: el puerto de acceso completo (8097) no abria. Al abrir un puerto,
`http.server` pide el nombre de la maquina y en la imagen del add-on esa
consulta devuelve a ratos bytes sueltos (UnicodeDecodeError)."""
import os, socket, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import main  # noqa: E402

GARBAGE = UnicodeDecodeError("utf-8", b"\xb8", 0, 1, "invalid start byte")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerStartRetry(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(main.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def test_real_server_opens_the_same_port_after_failed_lookups(self):
        port = _free_port()
        with mock.patch("socket.getfqdn", side_effect=[GARBAGE, GARBAGE, "equipo"]) as lookup:
            server = main._retry_name_lookup(lambda: main.make_server("127.0.0.1", port, main.app, threaded=True))
        self.addCleanup(server.server_close)
        self.assertEqual(lookup.call_count, 3)
        self.assertEqual(server.socket.getsockname()[1], port)
        self.assertEqual(self.sleep.call_count, 2)

    def test_gives_up_after_the_last_attempt(self):
        start = mock.Mock(side_effect=GARBAGE)
        with self.assertRaises(UnicodeDecodeError):
            main._retry_name_lookup(start)
        self.assertEqual(start.call_count, main.SERVER_START_ATTEMPTS)

    def test_other_errors_are_not_retried(self):
        start = mock.Mock(side_effect=OSError("puerto ocupado"))
        with self.assertRaises(OSError):
            main._retry_name_lookup(start)
        self.assertEqual(start.call_count, 1)

    def test_full_access_server_survives_a_failed_lookup(self):
        server = mock.Mock()
        with mock.patch.object(main, "make_server", side_effect=[GARBAGE, server]) as make:
            main._run_full_access_server()
        self.assertEqual(make.call_count, 2)
        server.serve_forever.assert_called_once()

    def test_wallpanel_server_survives_a_failed_lookup(self):
        server = mock.Mock()
        with mock.patch.object(main, "make_server", side_effect=[GARBAGE, server]) as make:
            main._run_wallpanel_server()
        self.assertEqual(make.call_count, 2)
        server.serve_forever.assert_called_once()
