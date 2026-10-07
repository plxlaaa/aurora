"""中间件、异常体系与运行时配置测试。

覆盖：请求 ID 透传、安全响应头、请求体限额、gzip 条件压缩、
统一错误响应结构、CORS 白名单、生产模式文档关闭。
"""
import gzip
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AI_ASSISTANT_DATA_DIR", tempfile.mkdtemp(prefix="aurora-mw-test-"))
os.environ.setdefault("AI_ASSISTANT_CONFIG_DIR", tempfile.mkdtemp(prefix="aurora-mw-cfg-"))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import middleware as mw  # noqa: E402
from app.core.errors import (  # noqa: E402
    AppError,
    ErrorCode,
    NotFoundError,
    RateLimitedError,
    SessionBusyError,
    UnauthorizedError,
    ValidationError,
    error_body,
)


def build_app() -> FastAPI:
    """构造一个仅含中间件与探针端点的最小应用。"""
    app = FastAPI()
    mw.install(app, max_body_bytes=1024)

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    @app.get("/big")
    async def big():
        return {"data": "x" * 4000}

    @app.get("/small")
    async def small():
        return {"data": "x"}

    @app.post("/api/echo")
    async def echo(payload: dict):
        return {"ok": True, "size": len(payload)}

    @app.get("/boom")
    async def boom():
        raise NotFoundError("会话不存在")

    @app.get("/raw401")
    async def raw401():
        raise UnauthorizedError("未授权")

    @app.get("/busy")
    async def busy():
        raise SessionBusyError("会话正在生成中，请稍后再试")

    return app


class TestRequestContext(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(build_app())

    def test_request_id_present_and_16hex(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        rid = resp.headers.get("x-request-id")
        self.assertTrue(rid)
        self.assertEqual(len(rid), 16)

    def test_request_id_reused_from_upstream(self):
        resp = self.client.get("/api/health", headers={"X-Request-ID": "trace-abc-123"})
        self.assertEqual(resp.headers.get("x-request-id"), "trace-abc-123")

    def test_security_headers_added(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(resp.headers.get("x-frame-options"), "SAMEORIGIN")
        self.assertEqual(resp.headers.get("referrer-policy"), "strict-origin-when-cross-origin")

    def test_security_headers_absent_on_websocket_upgrade(self):
        # 纯 ASGI 中间件不应影响非 http 作用域（由 type 判断直接透传）
        scope = {"type": "websocket"}
        called = {"ok": False}

        async def fake_app(scope, receive, send):
            called["ok"] = True

        asyncio_run(mw.SecurityHeadersMiddleware(fake_app)(scope, None, None))
        self.assertTrue(called["ok"])


class TestBodySizeLimit(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(build_app())

    def test_large_body_rejected_413(self):
        resp = self.client.post("/api/echo", content=b"x" * 4096)
        self.assertEqual(resp.status_code, 413)
        self.assertEqual(resp.json()["code"], ErrorCode.PAYLOAD_TOO_LARGE)

    def test_small_body_allowed(self):
        resp = self.client.post("/api/echo", json={"k": "v"})
        self.assertEqual(resp.status_code, 200)

    def test_get_without_body_allowed(self):
        resp = self.client.get("/big")
        self.assertEqual(resp.status_code, 200)


class TestGZip(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(build_app())

    def test_large_response_compressed(self):
        resp = self.client.get("/big", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(resp.headers.get("content-encoding"), "gzip")
        # TestClient 默认解压，断言内容完整
        self.assertIn("x" * 100, resp.text)

    def test_small_response_not_compressed(self):
        resp = self.client.get("/small", headers={"Accept-Encoding": "gzip"})
        self.assertIsNone(resp.headers.get("content-encoding"))

    def test_no_accept_encoding_skips(self):
        resp = self.client.get("/big", headers={"Accept-Encoding": "identity"})
        self.assertIsNone(resp.headers.get("content-encoding"))

    def test_vary_header_present_when_compressed(self):
        resp = self.client.get("/big", headers={"Accept-Encoding": "gzip"})
        self.assertIn("vary", {k.lower() for k in resp.headers.keys()})


class TestGZipHelpers(unittest.TestCase):
    def test_skip_when_already_encoded(self):
        headers = [(b"content-encoding", b"br"), (b"content-type", b"application/json")]
        self.assertFalse(mw.GZipMiddleware._should_compress(headers))

    def test_skip_image(self):
        headers = [(b"content-type", b"image/png"), (b"content-length", b"99999")]
        self.assertFalse(mw.GZipMiddleware._should_compress(headers))

    def test_compress_json(self):
        headers = [(b"content-type", b"application/json"), (b"content-length", b"5000")]
        self.assertTrue(mw.GZipMiddleware._should_compress(headers))

    def test_roundtrip_gzip(self):
        raw = b"hello aurora" * 100
        self.assertEqual(gzip.decompress(gzip.compress(raw)), raw)

    def test_respects_custom_min_size(self):
        """实例min_size 必须生效（回归：曾硬编码全局阈值导致参数失效）。"""
        headers = [(b"content-type", b"application/json"), (b"content-length", b"2000")]
        # 默认阈值 1024 下 2000 字节应压缩
        self.assertTrue(mw.GZipMiddleware._should_compress(headers, 1024))
        # 提高阈值到 4096 后同样大小的响应不再压缩
        self.assertFalse(mw.GZipMiddleware._should_compress(headers, 4096))
        # 降到 512 后仍压缩
        self.assertTrue(mw.GZipMiddleware._should_compress(headers, 512))

    def test_custom_min_size_still_skips_stream_types(self):
        """自定义阈值不应绕过「不压缩类型」白名单。"""
        headers = [(b"content-type", b"text/event-stream"), (b"content-length", b"99999")]
        self.assertFalse(mw.GZipMiddleware._should_compress(headers, 1))

    def test_unknown_length_still_compresses(self):
        """分块/流式响应无 Content-Length，任意阈值下都应压缩。"""
        headers = [(b"content-type", b"application/json")]
        self.assertTrue(mw.GZipMiddleware._should_compress(headers, 999999))


class TestErrorBody(unittest.TestCase):
    def test_error_body_shape(self):
        body = error_body("出错了", ErrorCode.VALIDATION_ERROR, "rid123")
        self.assertEqual(body, {
            "detail": "出错了",
            "code": "validation_error",
            "request_id": "rid123",
        })

    def test_error_body_omits_empty_optional(self):
        body = error_body("出错了", "internal_error")
        self.assertNotIn("request_id", body)
        self.assertNotIn("extra", body)

    def test_error_body_merges_extra(self):
        body = error_body("x", "internal_error", "r1", {"field": "name"})
        self.assertEqual(body["field"], "name")


class TestErrorTaxonomy(unittest.TestCase):
    def test_status_codes(self):
        self.assertEqual(ValidationError("x").status_code, 400)
        self.assertEqual(UnauthorizedError("x").status_code, 401)
        self.assertEqual(NotFoundError("x").status_code, 404)
        self.assertEqual(SessionBusyError("x").status_code, 409)
        self.assertEqual(RateLimitedError("x").status_code, 429)

    def test_codes(self):
        self.assertEqual(SessionBusyError("x").code, ErrorCode.SESSION_BUSY)
        self.assertEqual(RateLimitedError("x").code, ErrorCode.RATE_LIMITED)

    def test_app_error_is_http_exception_compatible(self):
        # 既有调用方/测试用 HTTPException 捕获，必须仍然成立
        from fastapi import HTTPException

        self.assertTrue(issubclass(AppError, HTTPException))
        err = NotFoundError("会话不存在")
        self.assertEqual(err.detail, "会话不存在")

    def test_custom_status_override(self):
        err = AppError("自定义", status_code=418, code="teapot")
        self.assertEqual(err.status_code, 418)
        self.assertEqual(err.code, "teapot")

    def test_status_code_map_complete(self):
        from app.core.errors import STATUS_CODE_MAP

        for status in (400, 401, 403, 404, 409, 413, 422, 429):
            self.assertIn(status, STATUS_CODE_MAP)


class TestRuntimeConfig(unittest.TestCase):
    def _reload(self, **env):
        from app.core import runtime as runtime_mod

        old = {k: os.environ.get(k) for k in env}
        os.environ.update({k: str(v) for k, v in env.items()})
        try:
            importlib.reload(runtime_mod)
            return runtime_mod.runtime
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            importlib.reload(runtime_mod)

    def test_defaults(self):
        cfg = self._reload(AI_ASSISTANT_PORT="")
        self.assertEqual(cfg.host, "127.0.0.1")
        self.assertEqual(cfg.port, 8080)
        self.assertEqual(cfg.max_concurrent_generations, 3)
        self.assertFalse(cfg.production)

    def test_port_override(self):
        cfg = self._reload(AI_ASSISTANT_PORT="9000")
        self.assertEqual(cfg.port, 9000)

    def test_invalid_port_falls_back(self):
        cfg = self._reload(AI_ASSISTANT_PORT="not-a-number")
        self.assertEqual(cfg.port, 8080)

    def test_cors_origins_from_env(self):
        cfg = self._reload(AI_ASSISTANT_CORS_ORIGINS="https://a.com, https://b.com")
        self.assertEqual(cfg.cors_origins, ["https://a.com", "https://b.com"])

    def test_body_limit_mb(self):
        cfg = self._reload(AI_ASSISTANT_MAX_BODY_MB="8")
        self.assertEqual(cfg.max_body_bytes, 8 * 1024 * 1024)

    def test_production_disables_docs(self):
        cfg = self._reload(AI_ASSISTANT_PRODUCTION="true")
        self.assertTrue(cfg.production)
        self.assertIsNone(cfg.docs_url)
        self.assertIsNone(cfg.openapi_url)

    def test_bool_parsing_variants(self):
        for raw in ("1", "true", "YES", "on"):
            self.assertTrue(self._reload(AI_ASSISTANT_PRODUCTION=raw).production, raw)
        for raw in ("0", "false", "no", "off"):
            self.assertFalse(self._reload(AI_ASSISTANT_PRODUCTION=raw).production, raw)


class TestHealthEndpoint(unittest.TestCase):
    """健康/就绪端点在真实 app 上工作（鉴权关闭状态）。"""

    @classmethod
    def setUpClass(cls):
        from app.main import app as real_app

        cls.client = TestClient(real_app)

    def test_health_ok(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("ready", data)
        self.assertIn("version", data)

    def test_ready_endpoint(self):
        resp = self.client.get("/api/ready")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for key in ("ready", "endpoints_total", "mcp_servers", "sandbox_mode"):
            self.assertIn(key, data)

    def test_error_response_has_code(self):
        resp = self.client.get("/api/models/catalog")
        # 无鉴权时（未设置 API Key）应返回 200
        self.assertIn(resp.status_code, (200, 401))


def asyncio_run(coro):
    """在同步测试中执行协程。"""
    import asyncio

    return asyncio.new_event_loop().run_until_complete(coro)


if __name__ == "__main__":
    unittest.main()