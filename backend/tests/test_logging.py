"""结构化日志与运行时配置接线测试。

覆盖：JSON 日志字段与 request_id 上下文注入、文本格式、
slow_request_ms 是否真正下发到中间件、幂等初始化不重复叠加 handler。
"""
import io
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AI_ASSISTANT_DATA_DIR", tempfile.mkdtemp(prefix="aurora-log-test-"))
os.environ.setdefault("AI_ASSISTANT_CONFIG_DIR", tempfile.mkdtemp(prefix="aurora-log-cfg-"))

from app.core.logsetup import JsonFormatter, setup_logging  # noqa: E402
from app.core.middleware import request_id_var  # noqa: E402


class JsonLoggingTests(unittest.TestCase):
    """JSON 日志格式化器。"""

    def setUp(self):
        self.buf = io.StringIO()
        self.addCleanup(request_id_var.set, request_id_var.get())
        request_id_var.set("")

    def _records(self) -> list[dict]:
        return [json.loads(line) for line in self.buf.getvalue().strip().split("\n") if line.strip()]

    def test_basic_fields_present(self):
        setup_logging(level="INFO", json_output=True, stream=self.buf)
        logging.getLogger("aiassistant.t").info("端点探测成功")
        record = self._records()[0]
        for key in ("ts", "level", "logger", "msg"):
            self.assertIn(key, record)
        self.assertEqual(record["level"], "INFO")
        self.assertEqual(record["logger"], "aiassistant.t")
        self.assertEqual(record["msg"], "端点探测成功")

    def test_request_id_injected_from_context(self):
        """业务日志应自动带上当前请求 id，无需逐处传参。"""
        setup_logging(level="INFO", json_output=True, stream=self.buf)
        request_id_var.set("rid-abc-123")
        logging.getLogger("aiassistant.t").info("处理会话")
        self.assertEqual(self._records()[0]["request_id"], "rid-abc-123")

    def test_request_id_absent_outside_request(self):
        """非请求上下文（如启动阶段）不应输出空request_id 字段。"""
        setup_logging(level="INFO", json_output=True, stream=self.buf)
        logging.getLogger("aiassistant.t").info("启动完成")
        self.assertNotIn("request_id", self._records()[0])

    def test_extra_fields_preserved(self):
        """业务自定义字段（extra=...）应原样进入 JSON。"""
        setup_logging(level="INFO", json_output=True, stream=self.buf)
        logging.getLogger("aiassistant.t").info("探测", extra={"endpoint": "main", "count": 3})
        record = self._records()[0]
        self.assertEqual(record["endpoint"], "main")
        self.assertEqual(record["count"], 3)

    def test_unicode_preserved(self):
        setup_logging(level="INFO", json_output=True, stream=self.buf)
        logging.getLogger("aiassistant.t").info("会话不存在或为空")
        self.assertEqual(self._records()[0]["msg"], "会话不存在或为空")

    def test_exception_included(self):
        setup_logging(level="INFO", json_output=True, stream=self.buf)
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("aiassistant.t").exception("未处理异常")
        record = self._records()[0]
        self.assertIn("exc", record)
        self.assertIn("boom", record["exc"])

    def test_text_mode_not_json(self):
        """默认文本模式保持人类可读，不应输出 JSON。"""
        setup_logging(level="INFO", json_output=False, stream=self.buf)
        logging.getLogger("aiassistant.t").info("纯文本日志")
        out = self.buf.getvalue()
        self.assertIn("纯文本日志", out)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(out.strip())

    def test_setup_is_idempotent(self):
        """重复初始化只应保留一个 handler，避免日志重复输出。"""
        root = logging.getLogger()
        setup_logging(level="INFO", json_output=True, stream=self.buf)
        setup_logging(level="INFO", json_output=True, stream=self.buf)
        setup_logging(level="INFO", json_output=True, stream=self.buf)
        tagged = [h for h in root.handlers if getattr(h, "_aurora_handler", False)]
        self.assertEqual(len(tagged), 1, "重复 setup_logging 不应叠加 handler")

    def test_invalid_level_falls_back_to_info(self):
        setup_logging(level="NOT_A_LEVEL", json_output=True, stream=self.buf)
        self.assertEqual(logging.getLogger().level, logging.INFO)

    def test_access_log_carries_request_id(self):
        """回归：访问日志自身也必须带 request_id（曾因过早 reset 而丢失该字段）。"""
        import asyncio

        from app.core import middleware as mw

        sent: list[dict] = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def app(scope, recv, snd):
            await snd({"type": "http.response.start", "status": 200, "headers": []})
            await snd({"type": "http.response.body", "body": b"{}"})

        setup_logging(level="INFO", json_output=True, stream=self.buf)
        request_id_var.set("")
        middleware = mw.RequestContextMiddleware(app, slow_request_ms=30_000)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/sessions",
            "headers": [(b"x-request-id", b"rid-access-1")],
            "client": ("127.0.0.1", 1234),
        }
        asyncio.new_event_loop().run_until_complete(middleware(scope, receive, send))

        records = self._records()
        self.assertTrue(records, "应产生一条访问日志")
        self.assertEqual(records[0]["request_id"], "rid-access-1")
        self.assertEqual(records[0]["logger"], "aiassistant.http")
        # 上下文应在请求结束后复位，避免污染后续请求
        self.assertEqual(request_id_var.get(), "")

    def test_request_id_reset_after_request(self):
        """请求结束后上下文必须复位，避免线程复用时串号。"""
        import asyncio

        from app.core import middleware as mw

        async def send(_message):
            return None

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def app(scope, recv, snd):
            # 请求处理中应能读到当前请求的 id
            assert request_id_var.get() == "rid-x-1"
            await snd({"type": "http.response.start", "status": 204, "headers": []})
            await snd({"type": "http.response.body", "body": b""})

        setup_logging(level="INFO", json_output=True, stream=self.buf)
        middleware = mw.RequestContextMiddleware(app)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/health",
            "headers": [(b"x-request-id", b"rid-x-1")],
            "client": ("127.0.0.1", 1234),
        }
        asyncio.new_event_loop().run_until_complete(middleware(scope, receive, send))
        self.assertEqual(request_id_var.get(), "")

    def test_formatter_usable_standalone(self):
        """JsonFormatter 可独立挂到任意 handler 上使用。"""
        formatter = JsonFormatter()
        record = logging.LogRecord("x", logging.INFO, "p", 1, "直挂消息", (), None)
        request_id_var.set("rid-standalone")
        payload = json.loads(formatter.format(record))
        self.assertEqual(payload["msg"], "直挂消息")
        self.assertEqual(payload["request_id"], "rid-standalone")


class RuntimeWiringTests(unittest.TestCase):
    """运行时配置必须真正下发到中间件，避免「死配置」。"""

    def test_slow_request_ms_passed_to_middleware(self):
        """AI_ASSISTANT_SLOW_REQUEST_MS 应生效，而非恒为默认 30s。"""
        from fastapi import FastAPI

        from app.core import middleware as mw

        app = FastAPI()
        mw.install(app, max_body_bytes=1024 * 1024, slow_request_ms=1234)
        installed = [
            m for m in app.user_middleware if m.cls is mw.RequestContextMiddleware
        ]
        self.assertEqual(len(installed), 1)
        self.assertEqual(installed[0].kwargs["slow_request_ms"], 1234)

    def test_install_default_keeps_backward_compat(self):
        """不传slow_request_ms 时保持默认阈值（向后兼容）。"""
        from fastapi import FastAPI

        from app.core import middleware as mw

        app = FastAPI()
        mw.install(app, max_body_bytes=1024 * 1024)
        installed = [
            m for m in app.user_middleware if m.cls is mw.RequestContextMiddleware
        ]
        self.assertEqual(installed[0].kwargs["slow_request_ms"], 30_000)


if __name__ == "__main__":
    unittest.main()
