"""Corte de carga de seguridad de una bateria sin SOC: si falla (equipo
apagado, ni BLE ni nube), no se reintenta en cada ciclo. Caso real 1-nov: la
Max Patio apagada generaba un intento cada 2-4 s.
"""
import os, sys, unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import battery_exec as be  # noqa: E402


def _dist(b):
    return {"action": "charge", "per_battery": [
        {"id": b.id, "name": b.name, "soc_pct": None, "power_w": 0, "note": "sin SOC"}]}


class SafeStopRetry(unittest.TestCase):
    def setUp(self):
        for d in (be._last_command, be._soc_unavailable_since, be._safe_stop_failed_at):
            d.clear()
        self.b = be.Battery(id="mp", name="Max Patio", capacity_wh=2000, source="ecoflow")
        self.t0 = datetime(2026, 11, 1, 23, 0, tzinfo=timezone.utc)
        be._soc_unavailable_since["mp"] = self.t0 - timedelta(seconds=be.SOC_UNAVAILABLE_SAFE_STOP_SECONDS + 1)

    def _run(self, at, stop):
        class _DT(datetime):
            @classmethod
            def now(cls, tz=None): return at
        with mock.patch.object(be, "datetime", _DT), mock.patch.object(be, "_safe_stop_charge", stop):
            be.execute([self.b], _dist(self.b), dry_run=False)

    def test_failed_stop_waits_before_retrying(self):
        stop = mock.Mock(side_effect=RuntimeError("EcoFlow no confirmo el corte de carga"))
        self._run(self.t0, stop)
        self._run(self.t0 + timedelta(seconds=3), stop)
        self._run(self.t0 + timedelta(seconds=30), stop)
        self.assertEqual(stop.call_count, 1)
        self._run(self.t0 + timedelta(seconds=be.SAFE_STOP_RETRY_SECONDS), stop)
        self.assertEqual(stop.call_count, 2)

    def test_success_is_not_resent_until_refresh(self):
        stop = mock.Mock()
        self._run(self.t0, stop)
        self._run(self.t0 + timedelta(seconds=90), stop)
        self.assertEqual(stop.call_count, 1)


if __name__ == "__main__":
    unittest.main()
