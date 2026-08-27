"""llm 模块单元测试：工具降级重试与错误详情。"""
import json
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.llm import LLMClient, EndpointInfo  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code, body, request=None):
        self.status_code = status_code
        self.reason_phrase = "Bad Request" if status_code == 400 else "OK"
        self.content = body
        self.request = request or httpx.Request("POST", "http://x/chat/completions")

    async def aread(self):
        return self.content

    async def aiter_lines(self):
        for line in self.content.decode("utf-8", "ignore").splitlines():
            yield line

    def json(self):
        return json.loads(self.content.decode("utf-8", "ignore"))

    def raise_for_status(self):
        pass


class _FakeStream:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *args):
        return False


class _FakeHttpClient:
    """记录每次请求的 payload，按脚本返回响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def post(self, url, json=None):
        self.requests.append(json)
        return self.responses.pop(0)

    def stream(self, method, url, json=None):
        self.requests.append(json)
        return _FakeStream(self.responses.pop(0))


def make_client(first_status, first_body, second_status=200, second_body=b""):
    endpoint = EndpointInfo(
        name="main",
        base_url="https://example.com/v1",
        api_key="",
        model_name="test-model",
    )
    client = LLMClient(endpoint)
    if second_status == 200:
        second_body = second_body or b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n'
    fake_http = _FakeHttpClient(
        [
            _FakeResponse(first_status, first_body),
            _FakeResponse(second_status, second_body),
        ]
    )
    client._client = fake_http
    return client, fake_http


class TestToolsFallback(unittest.IsolatedAsyncioTestCase):
    async def test_stream_retries_without_tools_on_blocked(self):
        blocked_body = b'{"code":20037,"message":"Function call is not supported for this model."}'
        client, fake_http = make_client(400, blocked_body)
        messages = [{"role": "user", "content": "hi"}]
        tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]

        chunks = []
        async for chunk in client.chat_completion_stream(messages, tools=tools):
            chunks.append(chunk)

        self.assertEqual(len(fake_http.requests), 2)
        self.assertIn("tools", fake_http.requests[0])
        self.assertNotIn("tools", fake_http.requests[1])
        self.assertIn("test-model", client._tools_blocked)
        self.assertEqual(chunks[0]["choices"][0]["delta"]["content"], "ok")

    async def test_stream_skips_tools_after_blocked_model(self):
        client, fake_http = make_client(200, b"")
        client._tools_blocked.add("test-model")
        tools = [{"type": "function", "function": {"name": "f", "parameters": {}}}]

        async for _ in client.chat_completion_stream([{"role": "user", "content": "hi"}], tools=tools):
            pass

        self.assertEqual(len(fake_http.requests), 1)
        self.assertNotIn("tools", fake_http.requests[0])

    async def test_non_stream_retries_without_tools_on_blocked(self):
        blocked_body = b'{"code":20037,"message":"Function call is not supported for this model."}'
        client, fake_http = make_client(
            400, blocked_body, second_body=b'{"choices":[{"message":{"content":"ok"}}]}'
        )

        result = await client.chat_completion(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "f", "parameters": {}}}],
        )

        self.assertEqual(result["choices"][0]["message"]["content"], "ok")
        self.assertIn("tools", fake_http.requests[0])
        self.assertNotIn("tools", fake_http.requests[1])

    async def test_non_blocked_error_raises_with_body(self):
        client, fake_http = make_client(
            400,
            b'{"code":20015,"message":"Input tag \'thinking\' not allowed."}',
            second_body=b"",
        )
        with self.assertRaises(httpx.HTTPStatusError) as ctx:
            await client.chat_completion([{"role": "user", "content": "hi"}], tools=None)
        self.assertIn("Input tag 'thinking'", str(ctx.exception))

    async def test_unrelated_400_with_tools_raises_no_retry(self):
        client, fake_http = make_client(400, b'{"code":20015,"message":"bad role"}', second_body=b"")
        with self.assertRaises(httpx.HTTPStatusError):
            async for _ in client.chat_completion_stream(
                [{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "f", "parameters": {}}}],
            ):
                pass
        self.assertEqual(len(fake_http.requests), 1)


if __name__ == "__main__":
    unittest.main()
