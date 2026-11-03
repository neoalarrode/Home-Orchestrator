"""Los ids de mensaje que llegan a HA son SIEMPRE crecientes aunque varios hilos
envien a la vez (3-nov 09:14: Lighting recibio `id_reuse`, "Identifier values
have to increase", y se perdio una orden de luz)."""
import json, os, sys, threading, time, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import ha_websocket as hw  # noqa: E402


class _SlowWS:
    """Socket que tarda un poco en cada envio: abre la ventana de la carrera."""
    def __init__(self, client):
        self.client, self.sent = client, []

    def send(self, raw):
        time.sleep(0.001)
        m = json.loads(raw)
        self.sent.append(m["id"])
        q = self.client._pending.get(m["id"])
        if q is not None:
            q.put_nowait({"id": m["id"], "type": "result", "success": True, "result": None})


class IncreasingIds(unittest.TestCase):
    def test_concurrent_calls_reach_ha_in_increasing_order(self):
        c = hw.HAWebSocketClient.__new__(hw.HAWebSocketClient)
        c._id_lock, c._send_lock = threading.Lock(), threading.Lock()
        c._next_id, c._pending, c.connected = 0, {}, True
        c._ws = _SlowWS(c)
        real_next = hw.HAWebSocketClient._next_msg_id

        def slow_next(self=c):
            mid = real_next(self)
            time.sleep(0.001)             # ensancha la ventana entre asignar y enviar
            return mid
        c._next_msg_id = slow_next
        threads = [threading.Thread(target=lambda: [c.call("call_service", domain="light", service="turn_on")
                                                    for _ in range(20)]) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(c._ws.sent), 160)
        self.assertEqual(c._ws.sent, sorted(c._ws.sent))
        self.assertEqual(c._pending, {})


if __name__ == "__main__":
    unittest.main()
