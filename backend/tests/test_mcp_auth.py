"""MCP 工具名解析与 JWT 密钥派生的回归测试。"""
import asyncio
import importlib
import os
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.mcp_client import MCPManager  # noqa: E402


class _FakeSettings:
    """仅提供 MCPManager 需要的两个读取接口。"""

    def __init__(self, servers: list[dict[str, Any]]) -> None:
        self._servers = servers

    def mcp_servers(self) -> list[dict[str, Any]]:
        return self._servers

    def raw(self, _section: str) -> dict[str, Any]:
        return {}


class _FakeHandle:
    """替换真实句柄，记录被调用的工具名。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, arguments))
        return {"server": self.name, "tool": tool}


class MCPDispatchTest(unittest.TestCase):
    def _manager(self, names: list[str]) -> tuple[MCPManager, dict[str, _FakeHandle]]:
        mgr = MCPManager(_FakeSettings([]))
        handles = {n: _FakeHandle(n) for n in names}
        mgr._handles = handles  # noqa: SLF001 测试直接注入假句柄
        return mgr, handles

    def test_server_name_with_underscore(self):
        """server 名含下划线时应完整匹配，而非被 split 截断。"""
        mgr, handles = self._manager(["my_server"])
        result = asyncio.run(mgr.dispatch("mcp_my_server_do", {"a": 1}))
        self.assertEqual(result["server"], "my_server")
        self.assertEqual(handles["my_server"].calls, [("do", {"a": 1})])

    def test_tool_name_with_underscore(self):
        """工具名含下划线时应保留完整工具名。"""
        mgr, handles = self._manager(["fs"])
        asyncio.run(mgr.dispatch("mcp_fs_read_text_file", {}))
        self.assertEqual(handles["fs"].calls[0][0], "read_text_file")

    def test_longest_prefix_wins(self):
        """存在 my 与 my_server 两个 server 时，应选更长的前缀。"""
        mgr, handles = self._manager(["my", "my_server"])
        result = asyncio.run(mgr.dispatch("mcp_my_server_do", {}))
        self.assertEqual(result["server"], "my_server")
        self.assertEqual(handles["my"].calls, [])

    def test_hyphen_server_name_still_works(self):
        """连字符命名（当前 config/mcp.yaml 的写法）不应回退。"""
        mgr, _ = self._manager(["example-stdio"])
        result = asyncio.run(mgr.dispatch("mcp_example-stdio_ping", {}))
        self.assertEqual(result["server"], "example-stdio")

    def test_unknown_server_raises(self):
        mgr, _ = self._manager(["fs"])
        with self.assertRaises(KeyError):
            asyncio.run(mgr.dispatch("mcp_other_do", {}))

    def test_non_mcp_prefix_raises(self):
        mgr, _ = self._manager(["fs"])
        with self.assertRaises(KeyError):
            asyncio.run(mgr.dispatch("run_python", {}))

    def test_missing_tool_part_raises(self):
        """只有 server 名、没有工具名时应拒绝。"""
        mgr, _ = self._manager(["fs"])
        with self.assertRaises(KeyError):
            asyncio.run(mgr.dispatch("mcp_fs_", {}))


class JWTSecretTest(unittest.TestCase):
    """JWT_SECRET 必须在进程重启后保持稳定（否则登录态全丢）。"""

    def setUp(self):
        self._saved = {
            k: os.environ.get(k)
            for k in ("AI_ASSISTANT_API_KEY", "AI_ASSISTANT_JWT_SECRET")
        }

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(importlib.import_module("app.core.auth"))

    def _reload(self):
        return importlib.reload(importlib.import_module("app.core.auth"))

    def test_secret_stable_across_restart_with_api_key(self):
        os.environ["AI_ASSISTANT_API_KEY"] = "test-key-123"
        os.environ.pop("AI_ASSISTANT_JWT_SECRET", None)
        first = self._reload()
        token = first.create_jwt("alice")
        second = self._reload()  # 模拟进程重启
        self.assertEqual(first.JWT_SECRET, second.JWT_SECRET)
        claims = second.verify_token(token)
        self.assertIsNotNone(claims)
        self.assertEqual(claims["sub"], "alice")

    def test_explicit_secret_takes_precedence(self):
        os.environ["AI_ASSISTANT_API_KEY"] = "test-key-123"
        os.environ["AI_ASSISTANT_JWT_SECRET"] = "explicit-secret"
        mod = self._reload()
        self.assertEqual(mod.JWT_SECRET, "explicit-secret")

    def test_secret_differs_per_api_key(self):
        os.environ.pop("AI_ASSISTANT_JWT_SECRET", None)
        os.environ["AI_ASSISTANT_API_KEY"] = "key-a"
        secret_a = self._reload().JWT_SECRET
        os.environ["AI_ASSISTANT_API_KEY"] = "key-b"
        secret_b = self._reload().JWT_SECRET
        self.assertNotEqual(secret_a, secret_b)

    def test_token_signed_with_other_key_rejected(self):
        os.environ.pop("AI_ASSISTANT_JWT_SECRET", None)
        os.environ["AI_ASSISTANT_API_KEY"] = "key-a"
        token = self._reload().create_jwt("bob")
        os.environ["AI_ASSISTANT_API_KEY"] = "key-b"
        self.assertIsNone(self._reload().verify_token(token))


if __name__ == "__main__":
    unittest.main()
