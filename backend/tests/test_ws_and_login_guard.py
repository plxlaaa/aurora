"""WebSocket 连接保活与登录限流清理测试（v1.3.0）。

覆盖：
1. **心跳保活**：客户端发送 ping 服务端回 pong；空闲超过阈值服务端主动关闭，
   避免静默掉线连接与生成任务长期残留。
2. **登录失败表清理**：过期的失败记录被周期性回收，防止长期运行内存无界增长。
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AI_ASSISTANT_DATA_DIR", tempfile.mkdtemp(prefix="aurora-ws-test-"))
os.environ.setdefault("AI_ASSISTANT_CONFIG_DIR", tempfile.mkdtemp(prefix="aurora-ws-cfg-"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.routers import api as api_module  # noqa: E402


class TestWebSocketHeartbeat(unittest.TestCase):
    """连接保活行为。"""

    def test_ping_pong(self):
        with TestClient(app) as client:
            with client.websocket_connect("/api/chat?session_id=wstest-ping") as ws:
                for _ in range(3):
                    ws.send_json({"type": "ping"})
                    self.assertEqual(ws.receive_json()["type"], "pong")

    def test_new_session_sends_welcome(self):
        with TestClient(app) as client:
            with client.websocket_connect("/api/chat") as ws:
                msg = ws.receive_json()
                self.assertEqual(msg["type"], "assistant_message")
                ws.send_json({"type": "ping"})
                self.assertEqual(ws.receive_json()["type"], "pong")

    def test_switch_model_ack(self):
        with TestClient(app) as client:
            with client.websocket_connect("/api/chat?session_id=wstest-model") as ws:
                ws.send_json({"type": "switch_model", "model_name": "qwen2.5:7b"})
                msg = ws.receive_json()
                self.assertEqual(msg["type"], "info")
                self.assertIn("qwen2.5:7b", msg["text"])

    def test_set_thinking_ack(self):
        with TestClient(app) as client:
            with client.websocket_connect("/api/chat?session_id=wstest-think") as ws:
                ws.send_json({"type": "set_thinking", "level": "high"})
                msg = ws.receive_json()
                self.assertEqual(msg["type"], "info")
                self.assertIn("high", msg["text"])

    def test_empty_message_rejected(self):
        with TestClient(app) as client:
            with client.websocket_connect("/api/chat?session_id=wstest-empty") as ws:
                ws.send_json({"type": "message", "content": "  "})
                msg = ws.receive_json()
                self.assertEqual(msg["type"], "error")

    def test_idle_timeout_defined(self):
        """空闲超时常量存在，且接收路径确实带超时。"""
        import inspect

        source = inspect.getsource(api_module)
        self.assertIn("WS_IDLE_TIMEOUT", source)
        self.assertIn("asyncio.wait_for", source)

    def test_idle_timeout_closes_connection(self):
        """空闲超时后必须主动关闭连接（结构性校验超时分支）。"""
        import inspect

        src = inspect.getsource(api_module.chat)
        self.assertIn("asyncio.TimeoutError", src)
        self.assertIn("ws.close(code=1001)", src)


class TestLoginFailureSweep(unittest.TestCase):
    """登录失败记录表的定期清理。"""

    def setUp(self):
        api_module._login_failures.clear()
        self._orig_sweep = api_module._login_last_sweep
        api_module._login_last_sweep = 0.0

    def tearDown(self):
        api_module._login_failures.clear()
        api_module._login_last_sweep = self._orig_sweep

    def test_expired_entries_removed(self):
        now = time.monotonic()
        old = now - api_module._LOGIN_LOCKOUT - 10
        api_module._login_failures["1.1.1.1"] = [old, old]
        api_module._login_failures["2.2.2.2"] = [now]
        # 强制触发清理
        api_module._login_last_sweep = 0.0
        api_module._sweep_login_failures(now)
        self.assertNotIn("1.1.1.1", api_module._login_failures)
        self.assertIn("2.2.2.2", api_module._login_failures)

    def test_recent_entries_kept(self):
        now = time.monotonic()
        api_module._login_failures["3.3.3.3"] = [now - 1]
        api_module._login_last_sweep = 0.0
        api_module._sweep_login_failures(now)
        self.assertIn("3.3.3.3", api_module._login_failures)

    def test_sweep_is_rate_limited(self):
        """清理有最小间隔，避免每次登录都全表扫描。"""
        now = time.monotonic()
        api_module._login_failures["4.4.4.4"] = [now - api_module._LOGIN_LOCKOUT - 10]
        api_module._login_last_sweep = now  # 刚刚清理过
        api_module._sweep_login_failures(now + 1)
        # 间隔未到，本轮不应清理
        self.assertIn("4.4.4.4", api_module._login_failures)

    def test_empty_table_safe(self):
        api_module._login_last_sweep = 0.0
        api_module._sweep_login_failures(time.monotonic())  # 不应抛异常

    def test_guard_triggers_sweep(self):
        """正常调用 _login_guard 也会顺带触发清理。"""
        now = time.monotonic()
        api_module._login_failures["5.5.5.5"] = [now - api_module._LOGIN_LOCKOUT - 10]
        api_module._login_last_sweep = 0.0
        api_module._login_guard("9.9.9.9")
        self.assertNotIn("5.5.5.5", api_module._login_failures)


if __name__ == "__main__":
    unittest.main()
