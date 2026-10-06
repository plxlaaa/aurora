"""tool_registry 模块单元测试：目录生成、分发路由与名称冲突。"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.tool_registry import ToolRegistry  # noqa: E402


class _FakeSkills:
    def __init__(self):
        self.skills = {}

    def directory_entries(self):
        return [{"name": n, "description": d} for n, d in self.skills.items()]

    def read_skill(self, name):
        if name in self.skills:
            return {"name": name, "description": self.skills[name], "doc": "用法"}
        return {"error": f"skill 不存在: {name}"}

    def get(self, name):
        return name if name in self.skills else None

    async def execute(self, name, arguments):
        return {"result": f"{name}:{arguments.get('city', '')}"}

    def list(self):
        return [{"name": n, "enabled": True, "description": d} for n, d in self.skills.items()]


class _FakeMCP:
    def __init__(self, tools=None):
        self._tools = tools or []

    def function_definitions(self):
        return [
            {
                "type": "function",
                "function": {"name": n, "description": f"MCP 工具 {n}", "parameters": {"type": "object", "properties": {}}},
            }
            for n in self._tools
        ]

    async def dispatch(self, name, arguments):
        return {"result": f"mcp:{name}"}


class _FakeCustom:
    def __init__(self, tools=None):
        self._tools = tools or {}

    def function_definitions(self):
        return [
            {
                "type": "function",
                "function": {"name": n, "description": "自定义", "parameters": {"type": "object", "properties": {}}},
            }
            for n, t in self._tools.items()
            if t.get("enabled", True)
        ]

    def directory_entries(self):
        return [
            {"name": n, "description": t.get("description", ""), "enabled": t.get("enabled", True)}
            for n, t in self._tools.items()
        ]

    def get(self, name):
        return self._tools.get(name)

    async def execute(self, name, arguments):
        return {"result": f"custom:{name}"}


class _FakeExecutor:
    def __init__(self):
        self.last_code = None

    async def execute(self, code, *, script=False):
        self.last_code = code

        class R:
            stdout = "hello\n"
            stderr = ""
            exit_code = 0

        return R()


def make_registry():
    skills = _FakeSkills()
    skills.skills["weather"] = "查天气"
    mcp = _FakeMCP(["mcp_fs_read"])
    custom = _FakeCustom({"query_stock": {"description": "查行情", "enabled": True}})
    executor = _FakeExecutor()
    return ToolRegistry(skills, mcp, custom, executor=executor)


class TestCollectAndDirectory(unittest.TestCase):
    def test_collect_tools_includes_all_sources(self):
        reg = make_registry()
        names = {d["function"]["name"] for d in reg.collect_tools()}
        self.assertEqual(names, {"read_skill", "run_skill", "run_code", "mcp_fs_read", "query_stock"})

    def test_directory_sections(self):
        reg = make_registry()
        text = reg.build_directory()
        self.assertIn("read_skill", text)
        self.assertIn("weather", text)
        self.assertIn("mcp_fs_read", text)
        self.assertIn("query_stock", text)
        self.assertIn("tool_call", text)
        self.assertIn("tool_result", text)

    def test_directory_omits_empty_sections(self):
        reg = ToolRegistry(_FakeSkills(), _FakeMCP([]), _FakeCustom({}))
        text = reg.build_directory()
        # 空 skills / MCP / 自定义分组整体省略
        self.assertNotIn("## Skills", text)
        self.assertNotIn("## MCP 工具", text)
        self.assertNotIn("## 自定义工具", text)
        self.assertIn("## 内置工具", text)


class TestDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_builtin_read_skill(self):
        reg = make_registry()
        result = await reg.dispatch("read_skill", {"name": "weather"})
        self.assertEqual(result["name"], "weather")

    async def test_dispatch_read_skill_alias_keys(self):
        # 模型键名变体兼容：skill / skill_name 均可
        reg = make_registry()
        self.assertEqual((await reg.dispatch("read_skill", {"skill": "weather"}))["name"], "weather")
        self.assertEqual((await reg.dispatch("read_skill", {"skill_name": "weather"}))["name"], "weather")

    async def test_dispatch_run_skill_alias_and_flat_args(self):
        # run_skill：skill 别名 + arguments 缺失时其余字段视为 skill 参数
        reg = make_registry()
        result = await reg.dispatch("run_skill", {"skill": "weather", "city": "北京"})
        self.assertEqual(result["result"], "weather:北京")

    async def test_dispatch_builtin_run_skill(self):
        reg = make_registry()
        result = await reg.dispatch("run_skill", {"name": "weather", "arguments": {"city": "北京"}})
        self.assertEqual(result["result"], "weather:北京")

    async def test_dispatch_builtin_run_code(self):
        reg = make_registry()
        result = await reg.dispatch("run_code", {"code": "print('hi')"})
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("hello", result["stdout"])

    async def test_dispatch_custom_and_mcp(self):
        reg = make_registry()
        self.assertEqual((await reg.dispatch("query_stock", {}))["result"], "custom:query_stock")
        self.assertEqual((await reg.dispatch("mcp_fs_read", {}))["result"], "mcp:mcp_fs_read")

    async def test_dispatch_fallback_to_skill_name(self):
        reg = make_registry()
        result = await reg.dispatch("weather", {"city": "上海"})
        self.assertEqual(result["result"], "weather:上海")

    async def test_dispatch_unknown_raises(self):
        reg = make_registry()
        with self.assertRaises(KeyError):
            await reg.dispatch("no_such_tool", {})


class TestNameExists(unittest.TestCase):
    def test_conflicts(self):
        reg = make_registry()
        self.assertTrue(reg.name_exists("read_skill"))
        self.assertTrue(reg.name_exists("run_code"))
        self.assertTrue(reg.name_exists("mcp_anything"))
        self.assertTrue(reg.name_exists("weather"))
        self.assertTrue(reg.name_exists("query_stock"))
        self.assertFalse(reg.name_exists("free_name"))


if __name__ == "__main__":
    unittest.main()
