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


if __name__ == "__main__":
    unittest.main()
