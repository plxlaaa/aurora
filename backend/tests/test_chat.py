"""chat 模块单元测试：消息归一化与工具降级。"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.chat import ChatPipeline, _normalize_messages  # noqa: E402


class TestNormalizeMessages(unittest.TestCase):
    def test_drops_non_standard_roles(self):
        raw = [
            {"role": "user", "content": "你好"},
            {"role": "thinking", "content": "思考中"},
            {"role": "assistant", "content": "回答"},
            {"role": "assistant_delta", "content": "增量"},
            {"role": "tool_call", "name": "f", "arguments": {}},
        ]
        normalized = _normalize_messages(raw)
        self.assertEqual([m["role"] for m in normalized], ["user", "assistant"])

    def test_keeps_tool_call_chain(self):
        raw = [
            {"role": "user", "content": "调用工具"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "name": "f", "content": "{}"},
        ]
        normalized = _normalize_messages(raw)
        self.assertEqual([m["role"] for m in normalized], ["user", "assistant", "tool"])
        self.assertEqual(normalized[1]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(normalized[2]["tool_call_id"], "call_1")
        self.assertEqual(normalized[2]["name"], "f")

    def test_keeps_user_attachments(self):
        raw = [{"role": "user", "content": "看这张图", "attachments": ["obj_1"]}]
        normalized = _normalize_messages(raw)
        self.assertEqual(normalized[0]["attachments"], ["obj_1"])

    def test_null_content_becomes_empty(self):
        normalized = _normalize_messages([{"role": "assistant", "content": None}])
        self.assertEqual(normalized[0]["content"], "")


class _FakeClient:
    def __init__(self):
        self.received = None

    async def chat_completion_stream(self, messages, **kwargs):
        self.received = {"messages": messages, "kwargs": kwargs}
        yield {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}


class _FakeRegistry:
    def __init__(self, client=None):
        self._client = client

    def get(self, name):
        return self._client


class _FakeCompressor:
    def should_compress(self, messages):
        return False

    async def compress(self, messages):
        return messages


class _FakeSkills:
    def function_definitions(self):
        return []

    async def execute(self, name, arguments):
        raise ValueError("no skill")


class _FakeMCP:
    def function_definitions(self):
        return []

    async def dispatch(self, name, arguments):
        raise ValueError("no mcp")


class TestPipelineNormalization(unittest.IsolatedAsyncioTestCase):
    async def test_run_full_passes_normalized_messages(self):
        client = _FakeClient()
        pipeline = ChatPipeline(
            _FakeRegistry(client),
            vision=None,
            compressor=_FakeCompressor(),
            skills=_FakeSkills(),
            mcp=_FakeMCP(),
        )
        sink = []

        async def record(event):
            sink.append(event)

        await pipeline.run_full(
            [
                {"role": "user", "content": "你好"},
                {"role": "thinking", "content": "思考"},
                {"role": "assistant", "content": "之前回答"},
            ],
            attachments=[],
            sink=record,
        )

        self.assertEqual(client.received["messages"][0]["role"], "user")
        self.assertEqual(client.received["messages"][1]["role"], "assistant")
        self.assertNotIn("thinking", [m["role"] for m in client.received["messages"]])
        self.assertEqual(sink[-1], {"type": "done"})


if __name__ == "__main__":
    unittest.main()
