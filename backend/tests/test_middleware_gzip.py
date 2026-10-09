"""gzip 中间件多块响应回归测试（v1.3.0 修复）。

背景：原实现对分块响应只在**最后一个非空块**上做压缩，却已在响应开始时
声明 `Content-Encoding: gzip`。客户端会把整条字节流按 gzip 解压，遇到未压缩
的前序块直接报`BadGzipFile` / `DecodingError`——大体积静态资源（前端 assets）
与任何流式响应都会损坏。

本测试在**原始 ASGI 层**驱动中间件（绕过 httpx 的自动解压），
直接校验「响应头声明」与「响应体字节」是否自洽。
"""
import asyncio
import gzip
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AI_ASSISTANT_DATA_DIR", tempfile.mkdtemp(prefix="aurora-gzip-test-"))
os.environ.setdefault("AI_ASSISTANT_CONFIG_DIR", tempfile.mkdtemp(prefix="aurora-gzip-cfg-"))

from app.core import middleware as mw  # noqa: E402
from app.core.middleware import GZipMiddleware  # noqa: E402

MB = 1024 * 1024


class _Collected(Exception):
    """承载异常路径下已收集到的 ASGI 消息。"""

    def __init__(self, result):
        super().__init__("collected")
        self.result = result


def _chunked_app(chunks, *, ctype="application/json", status=200, extra_headers=None):
    """构造一个把chunks 分多次发出的 ASGI 应用。"""

    async def app(scope, receive, send):
        headers = [(b"content-type", ctype.encode())]
        if extra_headers:
            headers += extra_headers
        await send({"type": "http.response.start", "status": status, "headers": headers})
        for i, chunk in enumerate(chunks):
            await send({
                "type": "http.response.body",
                "body": chunk,
                "more_body": i < len(chunks) - 1,
            })

    return app


def _run(app, *, accept_gzip=True, min_size=None):
    """驱动中间件，返回 (响应头字典, 响应体字节)。

    正常返回；若下游应用抛异常，则以 _Collected 携带已发出的消息抛出。
    """
    kwargs = {"min_size": min_size} if min_size is not None else {}
    middleware = GZipMiddleware(app, **kwargs)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "headers": [(b"accept-encoding", b"gzip, deflate")] if accept_gzip else [],
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    def collect():
        start = next((m for m in sent if m["type"] == "http.response.start"), None)
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        headers = {k.decode(): v.decode() for k, v in (start or {"headers": []})["headers"]}
        return headers, body

    try:
        asyncio.run(middleware(scope, receive, send))
    except BaseException:
        raise _Collected(collect()) from None
    return collect()


class _GzipAssertions(unittest.TestCase):
    """压缩相关断言（供各测试类继承）。"""

    def assertGzipRoundTrip(self, headers, body, original):
        self.assertEqual(headers.get("content-encoding"), "gzip")
        self.assertEqual(gzip.decompress(body), original)

    def assertPassthrough(self, headers, body, original):
        self.assertIsNone(headers.get("content-encoding"))
        self.assertEqual(body, original)


class TestGzipMultiChunk(_GzipAssertions):
    """多块响应：声明 gzip 就必须整体可解压。"""

    def test_single_chunk_compressed(self):
        payload = b"A" * 40000
        headers, body = _run(_chunked_app([payload]))
        self.assertGzipRoundTrip(headers, body, payload)

    def test_multi_chunk_compressed_and_decodable(self):
        """核心回归：多块响应整体可解压（原缺陷场景）。"""
        payload = b"B" * 4096
        chunks = [payload] * 5
        headers, body = _run(_chunked_app(chunks))
        self.assertGzipRoundTrip(headers, body, payload * 5)

    def test_multi_chunk_with_tiny_first_chunk(self):
        """首块极小、后续为大块：原实现会只压缩末块导致损坏。"""
        chunks = [b"x" * 10] + [b"C" * 20000] * 3
        headers, body = _run(_chunked_app(chunks))
        self.assertGzipRoundTrip(headers, body, b"".join(chunks))

    def test_many_large_chunks(self):
        chunks = [b"D" * 100000] * 4
        headers, body = _run(_chunked_app(chunks))
        self.assertGzipRoundTrip(headers, body, b"".join(chunks))

    def test_boundary_exactly_min_size(self):
        payload = b"T" * mw.GZIP_MIN_SIZE
        headers, body = _run(_chunked_app([payload]))
        self.assertGzipRoundTrip(headers, body, payload)

    def test_below_min_size_passthrough(self):
        payload = b"s" * (mw.GZIP_MIN_SIZE - 1)
        headers, body = _run(_chunked_app([payload]))
        self.assertPassthrough(headers, body, payload)

    def test_multi_chunk_below_min_size_total(self):
        """分块但总量不足阈值 → 透传且内容完整。"""
        chunks = [b"a" * 10] * 50  # 共 500B
        headers, body = _run(_chunked_app(chunks))
        self.assertPassthrough(headers, body, b"a" * 500)

    def test_custom_min_size_respected(self):
        payload = b"m" * 2000
        headers, body = _run(_chunked_app([payload]), min_size=4096)
        self.assertPassthrough(headers, body, payload)
        headers, body = _run(_chunked_app([payload]), min_size=1024)
        self.assertGzipRoundTrip(headers, body, payload)

    def test_streaming_content_types_skipped(self):
        for ctype in ("text/event-stream", "application/x-ndjson", "image/png", "application/zip"):
            with self.subTest(ctype=ctype):
                payload = b"E" * 5000
                headers, body = _run(_chunked_app([payload], ctype=ctype))
                self.assertPassthrough(headers, body, payload)

    def test_error_status_not_compressed(self):
        payload = b"X" * 5000
        headers, body = _run(_chunked_app([payload], status=500))
        self.assertPassthrough(headers, body, payload)

    def test_existing_content_encoding_preserved(self):
        payload = b"X" * 5000
        headers, body = _run(
            _chunked_app([payload], extra_headers=[(b"content-encoding", b"br")])
        )
        self.assertEqual(headers.get("content-encoding"), "br")
        self.assertEqual(body, payload)

    def test_client_without_gzip_support(self):
        payload = b"N" * 5000
        headers, body = _run(_chunked_app([payload]), accept_gzip=False)
        self.assertPassthrough(headers, body, payload)

    def test_content_length_predicates(self):
        payload = b"K" * 3000
        headers, body = _run(
            _chunked_app([payload], extra_headers=[(b"content-length", b"3000")])
        )
        self.assertGzipRoundTrip(headers, body, payload)

        small = b"q" * 500
        headers, body = _run(
            _chunked_app([small], extra_headers=[(b"content-length", b"500")])
        )
        self.assertPassthrough(headers, body, small)

    def test_vary_header_present_when_compressed(self):
        payload = b"V" * 5000
        headers, _ = _run(_chunked_app([payload]))
        self.assertEqual(headers.get("vary"), "accept-encoding")

    def test_content_length_matches_compressed_size(self):
        payload = b"L" * 8000
        headers, body = _run(_chunked_app([payload]))
        self.assertEqual(int(headers["content-length"]), len(body))


class TestGzipOversizeGuard(_GzipAssertions):
    """超大响应不应被整体读入内存。"""

    def test_oversize_without_content_length_not_buffered(self):
        cap = GZipMiddleware.MAX_BUFFER_BYTES
        payload = b"H" * (cap + 5000)
        headers, body = _run(_chunked_app([payload[:1 * MB], payload[1 * MB:]]))
        self.assertIsNone(headers.get("content-encoding"))
        self.assertEqual(len(body), len(payload))
        self.assertEqual(body, payload)

    def test_oversize_with_content_length_skips_buffering(self):
        cap = GZipMiddleware.MAX_BUFFER_BYTES
        payload = b"J" * (cap + 5000)
        headers, body = _run(
            _chunked_app([payload], extra_headers=[(b"content-length", str(len(payload)).encode())])
        )
        self.assertIsNone(headers.get("content-encoding"))
        self.assertEqual(body, payload)

    def test_just_under_cap_still_compressed(self):
        cap = GZipMiddleware.MAX_BUFFER_BYTES
        payload = b"L" * (cap - 10)
        headers, body = _run(_chunked_app([payload]))
        self.assertGzipRoundTrip(headers, body, payload)


class TestGzipEdgeCases(_GzipAssertions):
    """空响应、空块与异常中断。"""

    def test_no_body_response_declares_no_gzip(self):
        async def app(scope, receive, send):
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })

        headers, body = _run(app)
        self.assertIsNone(headers.get("content-encoding"))
        self.assertEqual(body, b"")

    def test_empty_body_message(self):
        headers, body = _run(_chunked_app([b""]))
        self.assertIsNone(headers.get("content-encoding"))
        self.assertEqual(body, b"")

    def test_app_exception_flushes_buffered_then_propagates(self):
        """应用中途抛异常：已缓冲内容须刷出且自洽，异常继续向上抛。"""
        payload = b"P" * 5000

        async def app(scope, receive, send):
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": payload, "more_body": True})
            raise RuntimeError("boom")

        with self.assertRaises(_Collected) as ctx:
            _run(app)
        headers, body = ctx.exception.result
        self.assertGzipRoundTrip(headers, body, payload)

    def test_send_wrapper_accepts_non_http_scope(self):
        """非 http scope（WebSocket/lifespan）应原样放行。"""

        async def app(scope, receive, send):
            await send({"type": "websocket.accept"})

        middleware = GZipMiddleware(app)
        sent = []

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            sent.append(message)

        asyncio.run(middleware({"type": "websocket"}, receive, send))
        self.assertEqual(sent, [{"type": "websocket.accept"}])


if __name__ == "__main__":
    unittest.main()
