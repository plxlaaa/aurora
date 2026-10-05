"""HookBus 与内置插件单元测试。"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AI_ASSISTANT_DATA_DIR", tempfile.mkdtemp(prefix="aurora-hooks-"))

from app.core.hooks import HookBus  # noqa: E402
from app.services.plugins.auto_title import AutoTitlePlugin  # noqa: E402
from app.services.plugins.stats import StatsPlugin  # noqa: E402


class TestHookBus(unittest.TestCase):
    def test_subscribe_and_emit(self):
        bus = HookBus()
        seen = []

        async def hook(payload):
            seen.append(payload["x"])

        bus.subscribe("generate.end", hook)
        asyncio.run(bus.emit("generate.end", {"x": 1}))
        self.assertEqual(seen, [1])

    def test_priority_order(self):
        bus = HookBus()
        order = []
        bus.subscribe("e", lambda p: order.append("late"), name="late", priority=200)
        bus.subscribe("e", lambda p: order.append("early"), name="early", priority=10)
        asyncio.run(bus.emit("e", {}))
        self.assertEqual(order, ["early", "late"])

    def test_error_isolated(self):
        bus = HookBus()

        def bad(payload):
            raise RuntimeError("boom")

        seen = []
        bus.subscribe("e", bad, name="bad")
        bus.subscribe("e", lambda p: seen.append(1), name="ok", priority=200)
        asyncio.run(bus.emit("e", {}))  # 不应抛异常
        self.assertEqual(seen, [1])

    def test_duplicate_subscribe_ignored(self):
        bus = HookBus()
        calls = []

        def hook(payload):
            calls.append(1)

        bus.subscribe("e", hook, name="same")
        bus.subscribe("e", hook, name="same")
        asyncio.run(bus.emit("e", {}))
        self.assertEqual(len(calls), 1)

    def test_sync_callback_supported(self):
        bus = HookBus()
        seen = []
        bus.subscribe("e", lambda p: seen.append(p["k"]))
        asyncio.run(bus.emit("e", {"k": "v"}))
        self.assertEqual(seen, ["v"])


class TestStatsPlugin(unittest.TestCase):
    def test_counters_aggregate(self):
        bus = HookBus()
        stats = StatsPlugin()
        stats.register(bus)

        async def run():
            await bus.emit("generate.start", {"session_id": "s1", "model": "m", "temperature": 0.7})
            await bus.emit("tool.after", {"session_id": "s1", "name": "weather", "ok": True, "duration_ms": 100})
            await bus.emit("tool.after", {"session_id": "s1", "name": "run_code", "ok": False, "duration_ms": 50})
            await bus.emit("generate.end", {"session_id": "s1", "content": "ok", "cancelled": False, "rounds": 2, "duration_ms": 1200})
            await bus.emit("generate.start", {"session_id": "s2", "model": "m", "temperature": 0.7})
            await bus.emit("generate.end", {"session_id": "s2", "content": "", "cancelled": True, "rounds": 1, "duration_ms": 300})

        asyncio.run(run())
        snap = stats.snapshot()
        self.assertEqual(snap["counters"]["generate_started"], 2)
        self.assertEqual(snap["counters"]["generate_completed"], 1)
        self.assertEqual(snap["counters"]["generate_cancelled"], 1)
        self.assertEqual(snap["counters"]["tool_calls"], 2)
        self.assertEqual(snap["counters"]["tool_errors"], 1)
        self.assertEqual(snap["avg_generate_ms"], 750)
        self.assertEqual(snap["active_sessions_tracked"], 2)


class TestAutoTitlePlugin(unittest.TestCase):
    def _make(self, history, fake_resp):
        sessions = mock.MagicMock()
        sessions.list_sessions.return_value = [{"id": "sid", "title": "新会话"}]
        sessions.load.return_value = history
        registry = mock.MagicMock()
        client = mock.MagicMock()

        async def chat_completion(messages, **kwargs):
            return fake_resp

        client.chat_completion.side_effect = chat_completion
        registry.get.return_value = client
        return AutoTitlePlugin(sessions, registry), sessions

    def test_renames_default_title(self):
        resp = {"choices": [{"message": {"content": " 北京天气查询 "}}]}
        plugin, sessions = self._make(
            [{"role": "user", "content": "查一下北京天气"},
             {"role": "assistant", "content": "晴"}],
            resp,
        )
        asyncio.run(plugin._title_session("sid"))
        sessions.rename.assert_called_once_with("sid", "北京天气查询")

    def test_truncates_long_title(self):
        resp = {"choices": [{"message": {"content": "这是一个非常非常长的超过十二个字的标题内容"}}]}
        plugin, sessions = self._make([{"role": "user", "content": "问题"}], resp)
        asyncio.run(plugin._title_session("sid"))
        args = sessions.rename.call_args[0]
        self.assertLessEqual(len(args[1]), 12)

    def test_skips_custom_title(self):
        sessions = mock.MagicMock()
        sessions.has_custom_title.return_value = True
        plugin = AutoTitlePlugin(sessions, mock.MagicMock())
        # 用户已自定义标题时不派发命名任务
        asyncio.run(plugin._on_generate_end({"session_id": "sid"}))
        self.assertNotIn("sid", plugin._inflight)


if __name__ == "__main__":
    unittest.main()
