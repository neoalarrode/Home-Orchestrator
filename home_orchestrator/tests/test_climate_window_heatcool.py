"""Deteccion de ventana sin sensor en zonas calor+frio (hallazgo R2#B): una
bajada rapida porque el AC esta ENFRIANDO no es una ventana abierta. El llamante
(zone_runner) ya no pasa wants_heat mientras la zona enfria/seca, ni wants_cool
mientras calienta; aqui se fija ese contrato del detector."""
import os, sys, unittest
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from climate.window_algorithm import WindowSlopeDetector  # noqa: E402

T0 = datetime(2026, 7, 1, 15, 0, tzinfo=timezone.utc)


def _drop(wants_heat, wants_cool):
    d = WindowSlopeDetector()
    temp, alert = 27.0, False
    for i in range(8):   # -0.2 C cada 2.5 min = -4.8 C/h
        alert = d.update(temp, T0 + timedelta(minutes=2.5 * i), wants_heat, wants_cool)
        temp -= 0.2
    return alert


class WindowHeatCool(unittest.TestCase):
    def test_fast_drop_while_cooling_is_not_a_window(self):
        # Zona calor+frio enfriando: el llamante pasa wants_heat=False.
        self.assertFalse(_drop(wants_heat=False, wants_cool=True))

    def test_fast_drop_while_resting_and_wanting_heat_is_a_window(self):
        self.assertTrue(_drop(wants_heat=True, wants_cool=True))


if __name__ == "__main__":
    unittest.main()
