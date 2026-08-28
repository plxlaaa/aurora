"""chat 模块单元测试：消息归一化、system 注入与 ReAct 端到端。"""
import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.chat import ChatPipeline, _normalize_messages, parse_bare_json_calls, parse_react_calls  # noqa: E402
from app.services.tool_registry import ToolRegistry  # noqa: E402


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


class TestParseReactCalls(unittest.TestCase):
    def test_single_call(self):
        text = '我先查一下。<tool_call>{"name": "weather", "arguments": {"city": "北京"}}</tool_call>'
        calls = parse_react_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "weather")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"city": "北京"})

    def test_multiple_calls(self):
        text = (
            '<tool_call>{"name":"a","arguments":{}}</tool_call>'
            '中间文字'
            '<tool_call>{"name":"b"}</tool_call>'
        )
        calls = parse_react_calls(text)
        self.assertEqual([c["function"]["name"] for c in calls], ["a", "b"])
        # 缺 arguments 时默认空对象
        self.assertEqual(json.loads(calls[1]["function"]["arguments"]), {})

    def test_invalid_json_skipped(self):
        text = '<tool_call>{"name": "a", "arguments":}</tool_call>'
        self.assertEqual(parse_react_calls(text), [])

    def test_no_marker(self):
        self.assertEqual(parse_react_calls("普通回复，没有调用。"), [])


class TestParseBareJsonCalls(unittest.TestCase):
    def test_plain_object(self):
        text = '{"name": "run_code", "arguments": {"code": "print(1)"}}'
        calls = parse_bare_json_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "run_code")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"code": "print(1)"})

    def test_with_trailing_notes(self):
        text = '{"name": "read_skill", "arguments": {"name": "weather"}}\n（调用工具查询）'
        calls = parse_bare_json_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "read_skill")

    def test_nested_arguments(self):
        text = '前言 {"name": "t", "arguments": {"a": {"b": 1}}} 后记'
        calls = parse_bare_json_calls(text)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"a": {"b": 1}})

    def test_missing_arguments_defaults_empty(self):
        calls = parse_bare_json_calls('{"name": "mcp_fs_read"}')
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {})

    def test_ignores_plain_json_without_name(self):
        self.assertEqual(parse_bare_json_calls('{"key": "value", "x": 1}'), [])
        self.assertEqual(parse_bare_json_calls("普通回复"), [])

    def test_multiline_arguments(self):
        text = '<tool_call>{\n  "name": "run_code",\n  "arguments": {"code": "print(1)"}\n}</tool_call>'
        calls = parse_react_calls(text)
        self.assertEqual(calls[0]["function"]["name"], "run_code")


class _FakeStreamClient:
    """按脚本顺序输出 content 分块的假 LLM 客户端。"""

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.received = []

    async def chat_completion_stream(self, messages, **kwargs):
        self.received.append({"messages": list(messages), "kwargs": kwargs})
        for text in self.scripts.pop(0):
            yield {"choices": [{"delta": {"content": text}}]}

    async def aclose(self):
        pass


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
    def __init__(self):
        self.executed = []

    def directory_entries(self):
        return [{"name": "weather", "description": "查天气"}]

    def read_skill(self, name):
        return {"name": name, "description": "查天气", "doc": "用法"}

    def get(self, name):
        return "weather" if name == "weather" else None

    async def execute(self, name, arguments):
        self.executed.append((name, arguments))
        return {"result": f"{name}-done"}

    def list(self):
        return []

    def function_definitions(self):
        return []


class _FakeMCP:
    def function_definitions(self):
        return []

    async def dispatch(self, name, arguments):
        return {"error": "no mcp"}


class _FakeCustom:
    def __init__(self):
        self.calls = []

    def function_definitions(self):
        return []

    def directory_entries(self):
        return []

    def get(self, name):
        return None

    async def execute(self, name, arguments):
        return {"error": "no custom"}


def make_pipeline(client, skills=None):
    skills = skills or _FakeSkills()
    tools = ToolRegistry(skills, _FakeMCP(), _FakeCustom())
    pipeline = ChatPipeline(_FakeRegistry(client), vision=None, compressor=_FakeCompressor(), tools=tools)
    return pipeline, skills


class TestPipelineReAct(unittest.IsolatedAsyncioTestCase):
    async def test_react_roundtrip(self):
        # 第一轮输出调用标记；第二轮输出最终回复
        client = _FakeStreamClient(
            [
                ['<tool_call>{"name": "read_skill", "arguments": {"name": "weather"}}</tool_call>'],
                ["根据文档，天气工具这样用：直接回答。"],
            ]
        )
        pipeline, skills = make_pipeline(client)
        sink = []

        async def record(event):
            sink.append(event)

        result = await pipeline.run_full(
            [{"role": "user", "content": "天气怎么样？"}], attachments=[], sink=record
        )

        # 两个流式请求
        self.assertEqual(len(client.received), 2)
        # system prompt 注入了工具目录
        first_system = client.received[0]["messages"][0]
        self.assertEqual(first_system["role"], "system")
        self.assertIn("read_skill", first_system["content"])
        self.assertIn("tool_call", first_system["content"])
        # 工具被执行并回填 user 消息（文本协议）
        tool_result_msg = client.received[1]["messages"][-1]
        self.assertEqual(tool_result_msg["role"], "user")
        self.assertIn("tool_result", tool_result_msg["content"])
        self.assertIn("read_skill", tool_result_msg["content"])
        # 最终回复正常结束
        self.assertTrue(result["done"])
        self.assertEqual(result["content"], "根据文档，天气工具这样用：直接回答。")
        types = [e["type"] for e in sink]
        self.assertIn("tool_call", types)
        self.assertEqual(sink[-1]["type"], "done")

    async def test_bare_json_fallback_roundtrip(self):
        # 模型省略 <tool_call> 标记直接输出裸 JSON，兜底解析后继续对话
        client = _FakeStreamClient(
            [
                ['{"name": "read_skill", "arguments": {"name": "weather"}}'],
                ["根据文档回答。"],
            ]
        )
        pipeline, skills = make_pipeline(client)
        sink = []

        async def record(event):
            sink.append(event)

        result = await pipeline.run_full([{"role": "user", "content": "工具怎么用？"}], attachments=[], sink=record)
        self.assertEqual(len(client.received), 2)
        tool_result_msg = client.received[1]["messages"][-1]
        self.assertEqual(tool_result_msg["role"], "user")
        self.assertIn("read_skill", tool_result_msg["content"])
        self.assertTrue(result["done"])

    async def test_bare_json_unknown_name_ignored(self):
        # 裸 JSON 但工具名未注册：视为普通文本直接结束
        client = _FakeStreamClient([['{"name": "no_such_tool", "arguments": {}}']])
        pipeline, _ = make_pipeline(client)

        async def record(event):
            pass

        result = await pipeline.run_full([{"role": "user", "content": "hi"}], attachments=[], sink=record)
        self.assertEqual(len(client.received), 1)
        self.assertIn("no_such_tool", result["content"])

    async def test_plain_reply_no_tools(self):
        client = _FakeStreamClient([["你好，有什么可以帮你？"]])
        pipeline, _ = make_pipeline(client)
        sink = []

        async def record(event):
            sink.append(event)

        result = await pipeline.run_full(
            [{"role": "user", "content": "hi"}], attachments=[], sink=record
        )
        self.assertEqual(len(client.received), 1)
        self.assertEqual(result["content"], "你好，有什么可以帮你？")
        self.assertEqual(sink[-1], {"type": "done"})

    async def test_system_merged_when_exists(self):
        client = _FakeStreamClient([["ok"]])
        pipeline, _ = make_pipeline(client)

        async def record(event):
            pass

        await pipeline.run_full(
            [{"role": "system", "content": "原有系统提示"}, {"role": "user", "content": "hi"}],
            attachments=[],
            sink=record,
        )
        system = client.received[0]["messages"][0]
        self.assertIn("read_skill", system["content"])
        self.assertIn("原有系统提示", system["content"])


if __name__ == "__main__":
    unittest.main()
