"""compress 模块单元测试。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.compress import (
    SUMMARY_TAG,
    ContextCompressor,
    estimate_tokens,
)


class _FakeRegistry:
    def __init__(self, client=None):
        self._client = client

    def get(self, name):
        return self._client


class _FakeClient:
    def __init__(self, reply="压缩后的摘要"):
        self.reply = reply
        self.calls = []

    async def chat_completion(self, messages, **kwargs):
        self.calls.append(messages)
        return {"choices": [{"message": {"content": self.reply}}]}


class _FakeSettings:
    def __init__(self, compress_cfg):
        self._compress = compress_cfg

    def compress(self):
        return self._compress


def make_messages(n):
    msgs = [{"role": "system", "content": "你是助手"}]
    for i in range(n):
        msgs.append({"role": "user", "content": f"问题 {i}"})
        msgs.append({"role": "assistant", "content": f"回答 {i}"})
    return msgs


class TestEstimateTokens(unittest.TestCase):
    def test_empty(self):
        self.assertGreaterEqual(estimate_tokens([]), 1)

    def test_simple(self):
        n = estimate_tokens([{"role": "user", "content": "你好 hello world"}])
        self.assertGreater(n, 0)

    def test_list_content(self):
        n = estimate_tokens([{"role": "user", "content": [{"type": "text", "text": "hi"}]}])
        self.assertGreater(n, 0)


class TestCompressor(unittest.TestCase):
    def test_should_compress_under_threshold(self):
        settings = _FakeSettings({"enabled": True, "threshold": 0.8, "context_window": 100000})
        c = ContextCompressor(settings, _FakeRegistry())
        self.assertFalse(c.should_compress(make_messages(5)))

    def test_should_compress_over_threshold(self):
        settings = _FakeSettings({"enabled": True, "threshold": 0.1, "context_window": 100})
        c = ContextCompressor(settings, _FakeRegistry())
        self.assertTrue(c.should_compress(make_messages(20)))

    def test_compress_keeps_system_and_recent(self):
        settings = _FakeSettings(
            {
                "enabled": True,
                "keep_recent_turns": 2,
                "strategy": "map_reduce",
                "prompt": "请压缩。",
            }
        )
        client = _FakeClient()
        c = ContextCompressor(settings, _FakeRegistry(client))
        messages = make_messages(10)
        result = c.compress(messages)
        # 需要异步运行
        import asyncio

        result = asyncio.run(result)
        # 系统提示被保留并含摘要标签
        self.assertEqual(result[0]["role"], "system")
        self.assertIn(SUMMARY_TAG, result[0]["content"])
        # 保留最近 2 轮 = 4 条消息
        non_system = [m for m in result if m["role"] != "system"]
        self.assertEqual(len(non_system), 4)
        # 最后一条是最近一轮的 assistant 回答
        self.assertEqual(non_system[-1]["content"], "回答 9")

    def test_truncate_strategy(self):
        settings = _FakeSettings(
            {
                "enabled": True,
                "keep_recent_turns": 1,
                "strategy": "truncate",
                "prompt": "请压缩。",
            }
        )
        c = ContextCompressor(settings, _FakeRegistry())
        import asyncio

        result = asyncio.run(c.compress(make_messages(10)))
        self.assertEqual(result[0]["role"], "system")
        non_system = [m for m in result if m["role"] != "system"]
        self.assertEqual(len(non_system), 2)


if __name__ == "__main__":
    unittest.main()
