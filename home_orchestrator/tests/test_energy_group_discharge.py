"""La descarga disponible se cuenta por GRUPO EcoFlow (una tarea y un limite de
1200 W por grupo), no por unidad. 1-nov: 4 STREAM sumaban 4800 W con el grupo
dando 1200; con la Ultra X en un grupo propio son 2400."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import battery_exec  # noqa: E402


def _b(i, main=None, source="ecoflow", w=1200):
    b = battery_exec.Battery.__new__(battery_exec.Battery)
    b.id, b.source, b.ecoflow_main_sn, b.max_discharge_w = i, source, main, w
    return b


class GroupDischarge(unittest.TestCase):
    def test_one_group_of_four(self):
        self.assertEqual(battery_exec.effective_max_discharge_w([_b(i, "G") for i in "abcd"]), 1200)

    def test_two_groups(self):
        bats = [_b("ultra", "U")] + [_b(i, "G") for i in "abc"]
        self.assertEqual(battery_exec.effective_max_discharge_w(bats), 2400)

    def test_four_independent(self):
        self.assertEqual(battery_exec.effective_max_discharge_w([_b(i, i) for i in "abcd"]), 4800)

    def test_non_ecoflow_adds_its_own(self):
        bats = [_b("a", "G"), _b("b", "G"), _b("h", source="ha", w=800)]
        self.assertEqual(battery_exec.effective_max_discharge_w(bats), 2000)


if __name__ == "__main__":
    unittest.main()
