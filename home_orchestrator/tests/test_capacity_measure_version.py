"""Las observaciones de capacidad medidas por la red (antes del 2-nov) se
descartan una vez; las nuevas se conservan."""
import os, sys, tempfile, unittest

_TMP = tempfile.mkdtemp(prefix="ho_cap_")
os.environ["CAPACITY_PATH"] = os.path.join(_TMP, "capacity.json")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import capacity_store as cs  # noqa: E402


class MeasureVersion(unittest.TestCase):
    def setUp(self):
        self.data = {"ecoflow:SN": {"name": "Ultra X", "segment_action": "discharge", "segment_start_soc": 96.0,
                                     "segment_energy_wh": 5000.0, "observations_charge": [6900.0] * 5,
                                     "observations_discharge": [4200.0] * 5}}
        self._load, self._save = cs._load, cs._save
        cs._load = lambda: self.data
        cs._save = lambda d: None

    def tearDown(self):
        cs._load, cs._save = self._load, self._save

    def test_old_observations_are_dropped_once(self):
        cs.update("ecoflow:SN", "Ultra X", 50.0, "charge", 10.0, declared_capacity_wh=3840)
        e = self.data["ecoflow:SN"]
        self.assertEqual(e["observations_charge"], [])
        self.assertEqual(e["observations_discharge"], [])
        self.assertEqual(e["measure_version"], cs.MEASURE_VERSION)
        self.assertEqual(e["segment_energy_wh"], 10.0)          # el tramo nuevo empieza limpio
        e["observations_charge"].append(3900.0)
        cs.update("ecoflow:SN", "Ultra X", 51.0, "charge", 10.0, declared_capacity_wh=3840)
        self.assertEqual(e["observations_charge"], [3900.0])    # no se vuelve a borrar


if __name__ == "__main__":
    unittest.main()
