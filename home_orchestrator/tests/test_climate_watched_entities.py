"""watched_entities solo debe devolver entidades REALES de HA (dominio.objeto).
Los delegados que son refs de puente internas (tuya:<id>) NO son entidades de HA:
si se cuelan en subscribe_entities, HA rechaza la llamada entera y el runner se
queda sin eventos (no conduce los equipos). Bug real tras un reinicio.
"""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import climate.zone_runner as zr  # noqa: E402
from climate import const as C  # noqa: E402


class _Stub:
    def __init__(self, zone):
        self.zone = zone
        self.watched_entities = zr.ZoneRunner.watched_entities.__get__(self)


class Watched(unittest.TestCase):
    def test_bridge_refs_excluded_ha_entities_kept(self):
        z = {
            C.CONF_CURRENT_TEMP_SENSOR: "sensor.salon_temp",
            C.CONF_PRESENCE_ENTITIES: ["binary_sensor.salon_pir"],
            C.CONF_CLIMATE_ENTITIES: ["tuya:0363626834987af0a082", "climate.un_ac_de_ha"],
            C.CONF_HUMIDIFIER_ENTITIES: ["humidifier.deerma", "tuya:deadbeef"],
        }
        w = _Stub(z).watched_entities()
        self.assertIn("sensor.salon_temp", w)
        self.assertIn("binary_sensor.salon_pir", w)
        self.assertIn("climate.un_ac_de_ha", w)
        self.assertIn("humidifier.deerma", w)
        self.assertNotIn("tuya:0363626834987af0a082", w)
        self.assertNotIn("tuya:deadbeef", w)
        self.assertTrue(all(":" not in e and "." in e for e in w), w)

    def test_is_ha_entity_id(self):
        self.assertTrue(zr._is_ha_entity_id("sensor.x"))
        self.assertTrue(zr._is_ha_entity_id("climate.airclima_12000_multi_indoor_connected_2"))
        self.assertFalse(zr._is_ha_entity_id("tuya:0363626834987af0a082"))
        self.assertFalse(zr._is_ha_entity_id("tuya:abc"))
        self.assertFalse(zr._is_ha_entity_id(None))
        self.assertFalse(zr._is_ha_entity_id("sin_punto"))


if __name__ == "__main__":
    unittest.main()
