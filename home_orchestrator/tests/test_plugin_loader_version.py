"""La version que muestra cada plugin es la del catalogo (plugins.json) cuando
corre el tag pineado: la fijada a mano se quedaba atras (Climate 'v0.7.8' con la
0.7.30 desplegada)."""
import os, sys, types, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import plugin_loader  # noqa: E402


class _P:
    def __init__(self):
        self.name, self.slug, self.version = "Climate", "climate", "0.7.8"


class VersionFromCatalog(unittest.TestCase):
    def run_load(self, active_tag):
        fake_dl = types.SimpleNamespace(current_tag=lambda slug: active_tag)
        with mock.patch.dict(sys.modules, {"plugin_downloader": fake_dl}), \
             mock.patch.object(plugin_loader, "PLUGIN_REGISTRY", {"climate": _P}), \
             mock.patch.object(plugin_loader, "_ensure_pinned_version", lambda slug: None), \
             mock.patch.object(plugin_loader, "catalogo", lambda: {"climate": {"version": "0.7.30", "tag": "v0.77.102"}}), \
             mock.patch("config_store.get_installed_plugins", lambda: ["climate"]):
            return plugin_loader.load_all_plugins()[0].version

    def test_pinned_tag_shows_catalog_version(self):
        self.assertEqual(self.run_load("v0.77.102"), "0.7.30")

    def test_other_tag_keeps_own_version(self):
        self.assertEqual(self.run_load("v0.77.35"), "0.7.8")


if __name__ == "__main__":
    unittest.main()
