"""Plantillas de las entidades auxiliares Tuya: si el JSON de estado no trae el
DP (el modelo declara mas de los que el equipo informa), la entidad conserva su
valor en vez de dejar un "Template variable warning" en el log de HA."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from tuya_native.ha_aux import build_aux  # noqa: E402


class AuxTemplates(unittest.TestCase):
    def tpl(self, **e):
        return build_aux(e, "dev", "N", "bt")["value_template"]

    def test_guarded_by_key_presence(self):
        for e in ({"code": "anion", "domain": "switch"}, {"code": "t", "domain": "sensor", "scale": 1},
                  {"code": "h", "domain": "number", "scale": 1}, {"code": "m", "domain": "select"}):
            t = self.tpl(**e)
            self.assertIn("'%s' in value_json" % e["code"], t)
            self.assertNotIn("value_json.%s " % e["code"], t)
            self.assertIn("this.state", t)

    def test_scale_kept(self):
        self.assertIn("| float / 10", self.tpl(code="t", domain="sensor", scale=1))

    def test_bool_domains_map_this_state(self):
        self.assertIn("'True' if this.state == 'on'", self.tpl(code="anion", domain="switch"))


class ClimateTemplates(unittest.TestCase):
    def cfg(self):
        from tuya_native.ha_climate import build_climate
        codes = {"switch": {"type": "bool"}, "temp_set": {"spec": {"min": 160, "max": 880, "scale": 1}},
                 "mode": {"spec": {"range": ["cold", "hot"]}}, "fan_speed_enum": {"spec": {"range": ["low", "high"]}},
                 "mode_ECO": {"type": "bool"}, "sleep": {"type": "bool"}}
        return build_climate("d", "N", codes, "bt")

    def test_preset_comes_from_active_bool_dp(self):
        t = self.cfg()["preset_mode_value_template"]
        self.assertNotIn("value_json.preset", t)
        self.assertIn("value_json.get('mode_ECO')", t)
        self.assertIn("none", t)

    def test_enum_preset_off_is_not_active(self):
        # AC Airclima: `sleep` es enum y vale "off" (Tuya Cloud, DP 105).
        t = self.cfg()["preset_mode_value_template"]
        self.assertIn("not in (none, false, 'off'", t)

    def test_fan_reads_the_real_dp(self):
        self.assertIn("fan_speed_enum", self.cfg()["fan_mode_state_template"])

    def test_celsius_unicode_normalised(self):
        cfg = build_aux({"code": "temp", "domain": "sensor", "unit": "℃"}, "d", "N", "bt")
        self.assertEqual((cfg["unit_of_measurement"], cfg.get("device_class")), ("°C", "temperature"))


if __name__ == "__main__":
    unittest.main()
