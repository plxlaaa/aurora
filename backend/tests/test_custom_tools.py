"""custom_tools 模块单元测试：校验、HTTP 执行与 Python runner。"""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.services.custom_tools as ct_mod  # noqa: E402
from app.services.custom_tools import CustomToolsManager, validate_tool  # noqa: E402
from app.services.sandbox import ExecResult  # noqa: E402


def make_manager(tmpdir):
    return CustomToolsManager(str(tmpdir))


class TestValidate(unittest.TestCase):
    def test_valid_http_tool(self):
        err = validate_tool(
            {
                "name": "query_stock",
                "description": "查询行情",
                "type": "http",
                "http": {"url": "https://api.example.com/{symbol}"},
            }
        )
        self.assertIsNone(err)

    def test_invalid_name(self):
        self.assertIsNotNone(validate_tool({"name": "1bad", "description": "x", "type": "http", "http": {"url": "u"}}))
        self.assertIsNotNone(validate_tool({"name": "read_skill", "description": "x", "type": "http", "http": {"url": "u"}}))

    def test_invalid_type(self):
        self.assertIsNotNone(validate_tool({"name": "ok_name", "description": "x", "type": "shell"}))

    def test_http_requires_url(self):
        self.assertIsNotNone(validate_tool({"name": "ok_name", "description": "x", "type": "http", "http": {}}))

    def test_python_requires_run(self):
        self.assertIsNotNone(validate_tool({"name": "ok_name", "description": "x", "type": "python", "code": "print(1)"}))
        self.assertIsNone(validate_tool({"name": "ok_name", "description": "x", "type": "python", "code": "def run(args):\n    return args"}))


class TestCRUD(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.mgr = make_manager(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_list(self):
        self.mgr.save(
            {
                "name": "query_stock",
                "description": "查询行情",
                "type": "http",
                "enabled": True,
                "http": {"url": "https://api.example.com/{symbol}", "method": "GET"},
            }
        )
        items = self.mgr.list()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "query_stock")
        # 持久化到磁盘
        mgr2 = CustomToolsManager(self.tmp.name)
        self.assertEqual(len(mgr2.list()), 1)

    def test_name_conflict_rejected(self):
        self.mgr.save({"name": "dup_tool", "description": "a", "type": "python", "code": "def run(a):\n    return 1"})
        with self.assertRaises(ValueError):
            self.mgr.save({"name": "dup_tool", "description": "b", "type": "python", "code": "def run(a):\n    return 2"})

    def test_delete_and_toggle(self):
        self.mgr.save({"name": "t1", "description": "a", "type": "python", "code": "def run(a):\n    return 1"})
        self.assertTrue(self.mgr.set_enabled("t1", False))
        self.assertFalse(self.mgr.function_definitions())
        self.assertTrue(self.mgr.delete("t1"))
        self.assertFalse(self.mgr.delete("t1"))


class _FakeResp:
    def __init__(self, status_code=200, text="", data=None):
        self.status_code = status_code
        self.text = text
        self._data = data
        self.is_redirect = False

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class _FakeAsyncClient:
    captured = []

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, method, url, **kwargs):
        type(self).captured.append({"method": method, "url": url, **kwargs})
        return _FakeResp(200, text="plain text", data={"price": 123})


class TestHTTPExecution(unittest.IsolatedAsyncioTestCase):
    async def test_http_get_query_params(self):
        mgr = CustomToolsManager.__new__(CustomToolsManager)
        mgr._tools = {}
        mgr._executor = None
        tool = {
            "name": "query_stock",
            "type": "http",
            "http": {"url": "https://api.example.com/stock/{symbol}", "method": "GET"},
        }
        # SSRF 校验会做真实 DNS 解析，测试中跳过（被 mock 的 httpx 不会真发请求）
        with mock.patch.object(ct_mod, "_assert_public_url", lambda url: None):
            with mock.patch.object(ct_mod, "httpx", SimpleNamespace(AsyncClient=_FakeAsyncClient, HTTPError=httpx.HTTPError)):
                result = await mgr._run_http(tool, {"symbol": "600519", "days": 5})
        self.assertIn("result", result)
        req = _FakeAsyncClient.captured[-1]
        self.assertEqual(req["method"], "GET")
        self.assertEqual(req["url"], "https://api.example.com/stock/600519")
        self.assertEqual(req["params"], {"days": 5})

    async def test_http_missing_param(self):
        mgr = CustomToolsManager.__new__(CustomToolsManager)
        tool = {"name": "t", "type": "http", "http": {"url": "https://api.example.com/{a}"}} 
        result = await mgr._run_http(tool, {})
        self.assertIn("error", result)


class TestSSRFGuard(unittest.TestCase):
    """自定义 HTTP 工具的 SSRF 防护。"""

    def test_blocks_loopback(self):
        with self.assertRaises(ValueError):
            ct_mod._assert_public_url("http://127.0.0.1:8080/admin")

    def test_blocks_private(self):
        with self.assertRaises(ValueError):
            ct_mod._assert_public_url("http://192.168.1.1/router")

    def test_blocks_link_local_metadata(self):
        with self.assertRaises(ValueError):
            ct_mod._assert_public_url("http://169.254.169.254/latest/meta-data")

    def test_blocks_bad_scheme(self):
        with self.assertRaises(ValueError):
            ct_mod._assert_public_url("file:///etc/passwd")

    def test_allows_public_ip_literal(self):
        # 公网 IP 字面量不拦截（不做外网 DNS，保证测试离线可跑）
        ct_mod._assert_public_url("http://93.184.216.34/api")


class _LocalExecutor:
    """直接用 python3 -c 执行生成脚本，真实验证 runner 行为。"""

    async def execute(self, code, *, script=False):
        proc = await asyncio.create_subprocess_exec(
            "python3", "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=20)
        return ExecResult(out.decode(), err.decode(), proc.returncode or 0)


class TestPythonExecution(unittest.IsolatedAsyncioTestCase):
    async def test_python_tool_success(self):
        mgr = CustomToolsManager.__new__(CustomToolsManager)
        mgr._executor = _LocalExecutor()
        tool = {
            "name": "calc_hash",
            "type": "python",
            "code": "def run(args):\n    import hashlib\n    return {'md5': hashlib.md5(args['text'].encode()).hexdigest()[:8]}",
        }
        result = await mgr._run_python(tool, {"text": "hello"})
        self.assertNotIn("error", result)
        self.assertEqual(len(result["result"]["md5"]), 8)

    async def test_python_tool_runtime_error(self):
        mgr = CustomToolsManager.__new__(CustomToolsManager)
        mgr._executor = _LocalExecutor()
        tool = {"name": "bad", "type": "python", "code": "def run(args):\n    raise ValueError('boom')"}
        result = await mgr._run_python(tool, {})
        self.assertIn("error", result)
        self.assertIn("boom", result["error"])

    async def test_python_tool_no_run_defined(self):
        mgr = CustomToolsManager.__new__(CustomToolsManager)
        mgr._executor = _LocalExecutor()
        tool = {"name": "no_run", "type": "python", "code": "def other(args):\n    return 1"}
        result = await mgr._run_python(tool, {})
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
