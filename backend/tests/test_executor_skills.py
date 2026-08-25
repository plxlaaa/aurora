"""skills 与 sandbox 单元测试。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.sandbox import Executor


class _FakeSettings:
    def __init__(self, sandbox_cfg):
        self._cfg = sandbox_cfg

    def sandbox(self):
        return self._cfg

    def sandbox_mode(self):
        return self._cfg.get("mode", "sandbox")


class TestExecutor(unittest.TestCase):
    def test_unknown_mode(self):
        settings = _FakeSettings({"mode": "bogus"})
        executor = Executor(settings)
        import asyncio

        result = asyncio.run(executor.execute("print(1)"))
        self.assertEqual(result.exit_code, -1)
        self.assertIn("未知执行模式", result.stderr)

    def test_local_disallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _FakeSettings(
                {"mode": "local", "local": {"allow": False, "workdir": tmp, "timeout": 10}}
            )
            executor = Executor(settings)
            import asyncio

            result = asyncio.run(executor.execute("print(1)"))
            self.assertEqual(result.exit_code, -1)
            self.assertIn("local 模式未开启", result.stderr)

    def test_local_allowed_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _FakeSettings(
                {"mode": "local", "local": {"allow": True, "workdir": tmp, "timeout": 10}}
            )
            executor = Executor(settings)
            import asyncio

            result = asyncio.run(executor.execute("print('hello')"))
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.stdout.strip(), "hello")
            self.assertEqual(result.mode, "local")

    def test_local_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _FakeSettings(
                {"mode": "local", "local": {"allow": True, "workdir": tmp, "timeout": 1}}
            )
            executor = Executor(settings)
            import asyncio

            result = asyncio.run(executor.execute("import time; time.sleep(5)"))
            self.assertTrue(result.timed_out)


class TestSkillsRun(unittest.TestCase):
    """通过本地执行器验证 weather / web_fetch skill 入口可运行。"""

    def test_weather_skill_entry(self):
        import asyncio
        import json

        from app.services.skills import SkillsManager

        skills_dir = Path(__file__).resolve().parents[2] / "config" / "skills"
        mgr = SkillsManager(str(skills_dir))
        names = {s.name for s in mgr._skills.values()}
        self.assertIn("weather", names)
        self.assertIn("web_fetch", names)

        # 直接以子进程方式验证入口解析 JSON 参数
        result = asyncio.run(mgr._run_entry(mgr._skills["weather"], {"city": "北京"}))
        # 网络不可用时应返回带 error 或 city 的结果，而非抛异常
        self.assertIn("city", result)

    def test_function_definition_schema(self):
        from app.services.skills import SkillsManager

        skills_dir = Path(__file__).resolve().parents[2] / "config" / "skills"
        mgr = SkillsManager(str(skills_dir))
        defs = mgr.function_definitions()
        by_name = {d["function"]["name"]: d for d in defs}
        self.assertIn("weather", by_name)
        params = by_name["weather"]["function"]["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertIn("city", params["required"])


if __name__ == "__main__":
    unittest.main()
