"""Settings 配置读写的隔离性与原子性测试。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402


class SettingsIsolationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config_dir = self._tmp.name
        with open(os.path.join(self.config_dir, "models.yaml"), "w", encoding="utf-8") as fh:
            yaml.safe_dump(
                {
                    "endpoints": {
                        "main": {"base_url": "http://main", "api_key": "secret-main"},
                        "vision": {"base_url": "http://vision", "api_key": "secret-vision"},
                    },
                    "compress": {"enabled": True, "threshold": 0.8},
                },
                fh,
                allow_unicode=True,
            )
        self.settings = Settings(config_dir=self.config_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def test_raw_returns_deep_copy(self):
        """原地修改 raw() 的返回值不应影响内存配置。"""
        first = self.settings.raw("models")
        first["endpoints"]["main"]["api_key"] = "tampered"
        first["endpoints"]["injected"] = {}
        second = self.settings.raw("models")
        self.assertEqual(second["endpoints"]["main"]["api_key"], "secret-main")
        self.assertNotIn("injected", second["endpoints"])

    def test_nested_lists_also_isolated(self):
        """列表类配置（如 mcp servers）同样不能被原地污染。"""
        self.settings.update("mcp", {"servers": [{"name": "a", "enabled": True}]})
        servers = self.settings.mcp_servers()
        servers.append({"name": "b"})
        servers[0]["enabled"] = False
        again = self.settings.mcp_servers()
        self.assertEqual(len(again), 1)
        self.assertTrue(again[0]["enabled"])

    def test_helper_accessors_isolated(self):
        """endpoints()/compress() 等便捷访问器也走深拷贝。"""
        self.settings.compress()["threshold"] = 0.1
        self.assertEqual(self.settings.compress()["threshold"], 0.8)

    def test_update_persists_and_reloads(self):
        current = self.settings.raw("models")
        current["compress"]["threshold"] = 0.5
        self.settings.update("models", current)
        self.assertEqual(self.settings.compress()["threshold"], 0.5)
        with open(os.path.join(self.config_dir, "models.yaml"), encoding="utf-8") as fh:
            on_disk = yaml.safe_load(fh)
        self.assertEqual(on_disk["compress"]["threshold"], 0.5)

    def test_update_failure_keeps_old_file_intact(self):
        """序列化失败时原文件必须保持可读，且不留下临时文件。"""

        class _Unserializable:
            pass

        with self.assertRaises(Exception):
            self.settings.update("models", {"bad": _Unserializable()})
        with open(os.path.join(self.config_dir, "models.yaml"), encoding="utf-8") as fh:
            on_disk = yaml.safe_load(fh)
        self.assertEqual(on_disk["endpoints"]["main"]["api_key"], "secret-main")
        leftovers = [n for n in os.listdir(self.config_dir) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_missing_section_returns_empty_dict(self):
        self.assertEqual(self.settings.raw("nonexistent"), {})


if __name__ == "__main__":
    unittest.main()
