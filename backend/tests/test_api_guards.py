"""登录速率限制测试：纯函数级验证 _login_guard 行为。"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# 导入 api 路由会实例化会话存储，重定向到临时目录（沙箱无 /opt 权限）
os.environ.setdefault("AI_ASSISTANT_DATA_DIR", tempfile.mkdtemp(prefix="aurora-test-"))

from fastapi import HTTPException  # noqa: E402

from app.routers import api as api_module  # noqa: E402


class TestLoginRateLimit(unittest.TestCase):
    def setUp(self):
        # 每个用例独立-ip，避免互相污染
        self.ip = f"10.0.0.{id(self) % 200 + 2}"
        api_module._login_failures.pop(self.ip, None)

    def tearDown(self):
        api_module._login_failures.pop(self.ip, None)

    def test_allows_below_limit(self):
        for _ in range(api_module._LOGIN_ATTEMPT_MAX - 1):
            api_module._login_failures.setdefault(self.ip, []).append(time.monotonic())
        api_module._login_guard(self.ip)  # 不应抛异常

    def test_blocks_at_limit(self):
        now = time.monotonic()
        api_module._login_failures[self.ip] = [now] * api_module._LOGIN_ATTEMPT_MAX
        with self.assertRaises(HTTPException) as ctx:
            api_module._login_guard(self.ip)
        self.assertEqual(ctx.exception.status_code, 429)

    def test_old_failures_expire(self):
        # 超出锁定窗口的失败不计入
        old = time.monotonic() - api_module._LOGIN_LOCKOUT - 1
        api_module._login_failures[self.ip] = [old] * api_module._LOGIN_ATTEMPT_MAX
        api_module._login_guard(self.ip)  # 不应抛异常


class TestBusySessionGuard(unittest.TestCase):
    def test_rejects_running_session(self):
        with mock.patch.object(
            api_module.sessions, "get_status", return_value=api_module.sessions.STATUS_RUNNING
        ):
            with self.assertRaises(HTTPException) as ctx:
                api_module._reject_busy_session("sid")
            self.assertEqual(ctx.exception.status_code, 409)

    def test_allows_idle_session(self):
        with mock.patch.object(
            api_module.sessions, "get_status", return_value=api_module.sessions.STATUS_IDLE
        ):
            api_module._reject_busy_session("sid")  # 不应抛异常


if __name__ == "__main__":
    unittest.main()
