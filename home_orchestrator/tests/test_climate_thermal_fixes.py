"""Arreglos del modelo termico con datos reales (31-oct):
1. Perdida pasiva AGREGADA: con un sensor que solo informa al cambiar 0.2 C, la
   mediana por tramo salia 0 ("retiene perfectamente").
2. Signo en las tasas: un tramo de calor en que la zona BAJA no es calentar.
3. Reposos largos troceados y recortados a la ventana de historico.
4. Exterior mas caliente que dentro (sensor al sol) no cuenta como perdida.
"""
import os, sys, unittest
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.thermal_model as tm  # noqa: E402

T0 = datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc)


class S:
    def __init__(self, state, t): self.state, self.last_changed = str(state), t


def quantized_cooling(hours, start_temp=21.0, k=0.02, outdoor=11.0, step=0.2):
    """Interior que se enfria hacia el exterior, informado solo al cambiar `step`."""
    pts, temp, last = [S(start_temp, T0)], start_temp, start_temp
    for m in range(1, int(hours * 60) + 1):
        temp += k * (outdoor - temp) / 60
        if abs(temp - last) >= step:
            last = round(temp / step) * step
            pts.append(S(last, T0 + timedelta(minutes=m)))
    return pts


class IdleLossPooled(unittest.TestCase):
    def test_quantized_sensor_no_longer_reads_zero(self):
        temps = quantized_cooling(48)
        outs = [S(11.0, T0)]
        runs = [(T0 + timedelta(hours=h), T0 + timedelta(hours=h, minutes=30), "off") for h in range(0, 48, 2)]
        k, n = tm._learn_idle_loss_coeff(temps, runs, outs)
        self.assertIsNotNone(k)
        self.assertAlmostEqual(k, 0.02, delta=0.008)    # antes: mediana 0.0

    def test_long_single_rest_is_chunked_and_clipped(self):
        temps = quantized_cooling(48)
        outs = [S(11.0, T0)]
        runs = [(T0 - timedelta(days=3), T0 + timedelta(hours=48), "off")]  # empieza ANTES del historico
        k, n = tm._learn_idle_loss_coeff(temps, runs, outs)
        self.assertGreaterEqual(n, 10)
        self.assertAlmostEqual(k, 0.02, delta=0.008)

    def test_sun_heated_outdoor_is_ignored(self):
        temps = quantized_cooling(24)
        outs = [S(35.0, T0)]                              # sensor exterior al sol
        runs = [(T0, T0 + timedelta(hours=24), "off")]
        self.assertIsNone(tm._learn_idle_loss_coeff(temps, runs, outs)[0])


class RateSign(unittest.TestCase):
    def test_heating_run_where_zone_falls_is_not_heating(self):
        temps = [S(21.0, T0), S(20.6, T0 + timedelta(minutes=40))]
        runs = [(T0, T0 + timedelta(minutes=40), "on")] * 3
        self.assertEqual(tm._learn_rate(temps, runs, sign=+1), (None, 0))

    def test_heating_run_where_zone_rises_counts(self):
        temps = [S(20.0, T0), S(20.6, T0 + timedelta(minutes=40))]
        runs = [(T0, T0 + timedelta(minutes=40), "on")] * 3
        rate, n = tm._learn_rate(temps, runs, sign=+1)
        self.assertEqual(n, 3); self.assertAlmostEqual(rate, 0.9, places=2)


class DryIsNotRest(unittest.TestCase):
    def test_dry_segments_are_excluded_from_passive_loss(self):
        log = [(T0.timestamp(), "off"), ((T0 + timedelta(hours=3)).timestamp(), "dry"),
               ((T0 + timedelta(hours=6)).timestamp(), "off")]
        runs = tm._runs_from_action_log(log, T0 + timedelta(hours=9), lambda a: a != "off")
        self.assertEqual([r[2] for r in runs], ["off", "on", "off"])


if __name__ == "__main__":
    unittest.main()
