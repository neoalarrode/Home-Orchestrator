"""12-nov: los procesos Node de Starlink morian en cada arranque -- las
dependencias instaladas eran x64 y la maquina ARM, y solo se instalaban si la
carpeta no existia."""
import os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import starlink_plugin  # noqa: E402


class NodeArch(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.modules = os.path.join(self.dir.name, "node_modules")
        p = mock.patch.object(starlink_plugin, "_NODE_DIR", self.dir.name)
        p.start(); self.addCleanup(p.stop)
        self.plugin = starlink_plugin.StarlinkPlugin.__new__(starlink_plugin.StarlinkPlugin)

    def _install(self, *esbuild):
        for name in esbuild:
            os.makedirs(os.path.join(self.modules, "@esbuild", name))
        os.makedirs(self.modules, exist_ok=True)

    def _ensure(self, machine):
        with mock.patch.object(starlink_plugin.platform, "machine", return_value=machine), \
                mock.patch.object(starlink_plugin.subprocess, "run") as run:
            self.plugin._ensure_node_deps()
        return run

    def test_x64_modules_on_arm_are_reinstalled(self):
        self._install("linux-x64")
        run = self._ensure("aarch64")
        run.assert_called_once()
        self.assertFalse(os.path.exists(self.modules))

    def test_matching_modules_are_kept(self):
        self._install("linux-arm64")
        self.assertEqual(self._ensure("aarch64").call_count, 0)
        self.assertTrue(os.path.isdir(self.modules))

    def test_x64_machine_keeps_x64_modules(self):
        self._install("linux-x64")
        self.assertEqual(self._ensure("x86_64").call_count, 0)

    def test_modules_without_esbuild_or_unknown_machine_are_kept(self):
        self._install()
        self.assertEqual(self._ensure("aarch64").call_count, 0)
        self._install("linux-x64")
        self.assertEqual(self._ensure("riscv64").call_count, 0)

    def test_missing_modules_are_installed(self):
        self._ensure("aarch64").assert_called_once()

    def test_a_dead_process_is_reported(self):
        proc = mock.Mock(stderr=["boom\n"]); proc.wait.return_value = 1
        with self.assertLogs("starlink_plugin", level="WARNING") as logs:
            self.plugin._log_stderr(proc, "historian")
        self.assertIn("historian", logs.output[0])

    def test_a_process_we_stopped_is_not_reported(self):
        proc = mock.Mock(stderr=[]); proc.wait.return_value = -15
        with self.assertNoLogs("starlink_plugin", level="WARNING"):
            self.plugin._log_stderr(proc, "historian")
