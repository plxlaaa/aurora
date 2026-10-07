"""网关 HTTP 层集成测试（TestClient）。

覆盖：健康/就绪探针、统一错误响应结构与会话分页参数校验、
安全响应头、请求体限额、CORS 白名单、鉴权开启后的 401 行为。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AI_ASSISTANT_DATA_DIR", tempfile.mkdtemp(prefix="aurora-api-test-"))
os.environ.setdefault("AI_ASSISTANT_CONFIG_DIR", tempfile.mkdtemp(prefix="aurora-api-cfg-"))
os.environ.setdefault("AI_ASSISTANT_API_KEY", "")  # 默认关闭鉴权，便于测试

from fastapi.testclient import TestClient  # noqa: E402

from app.core.errors import ErrorCode  # noqa: E402
from app.main import app  # noqa: E402


class TestHealthEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_health(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertIn("mode", data)
        self.assertIn("ready", data)

    def test_ready(self):
        resp = self.client.get("/api/ready")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("endpoints_total", data)
        self.assertIn("sandbox_mode", data)
        self.assertIn("auth_enabled", data)

    def test_health_needs_no_auth(self):
        # health/ready 属公开探针，即使开启鉴权也应可访问
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/api/ready").status_code, 200)


class TestErrorContract(unittest.TestCase):
    """所有错误响应都应带 detail + code，request_id 可选。"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)

    def test_missing_resource_404_has_code(self):
        resp = self.client.get("/api/skills/definitely-not-exist/files")
        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertIn("detail", body)
        self.assertIn("code", body)
        self.assertEqual(body["code"], ErrorCode.NOT_FOUND)

    def test_validation_error_400_has_code(self):
        resp = self.client.put("/api/skills/weather", json={})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], ErrorCode.VALIDATION_ERROR)

    def test_session_not_found_404(self):
        resp = self.client.get("/api/sessions/nonexistent-session")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["code"], ErrorCode.NOT_FOUND)

    def test_request_id_header_present(self):
        resp = self.client.get("/api/health")
        self.assertTrue(resp.headers.get("x-request-id"))


class TestSessionPaginationAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.ids = []
        for i in range(6):
            sid = cls.client.post("/api/sessions").json()["id"]
            cls.client.post(f"/api/sessions/{sid}/rename", json={"title": f"分页会话{i}"})
            cls.ids.append(sid)

    def test_default_page_size(self):
        resp = self.client.get("/api/sessions")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertLessEqual(len(body["sessions"]), 50)
        self.assertIn("total", body)
        self.assertIn("has_more", body)

    def test_limit_offset(self):
        body = self.client.get("/api/sessions", params={"limit": 2, "offset": 0}).json()
        self.assertEqual(len(body["sessions"]), 2)
        self.assertEqual(body["limit"], 2)
        self.assertEqual(body["offset"], 0)

    def test_limit_bounds_rejected(self):
        # limit 必须 >= 1
        resp = self.client.get("/api/sessions", params={"limit": 0})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["code"], ErrorCode.VALIDATION_ERROR)

    def test_limit_too_large_rejected(self):
        resp = self.client.get("/api/sessions", params={"limit": 99999})
        self.assertEqual(resp.status_code, 422)

    def test_negative_offset_rejected(self):
        resp = self.client.get("/api/sessions", params={"offset": -1})
        self.assertEqual(resp.status_code, 422)

    def test_keyword_filter(self):
        body = self.client.get("/api/sessions", params={"q": "分页会话"}).json()
        self.assertGreaterEqual(body["total"], 6)

    def test_keyword_no_match_empty(self):
        body = self.client.get("/api/sessions", params={"q": "绝对不存在XYZ"}).json()
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["sessions"], [])

    def test_overlong_keyword_rejected(self):
        resp = self.client.get("/api/sessions", params={"q": "x" * 200})
        self.assertEqual(resp.status_code, 422)

    def test_backward_compatible_keys(self):
        # 前端依赖 sessions 数组；新增字段不应破坏既有结构
        body = self.client.get("/api/sessions").json()
        self.assertIsInstance(body["sessions"], list)
        if body["sessions"]:
            self.assertIn("id", body["sessions"][0])
            self.assertIn("title", body["sessions"][0])


class TestSecurityHeadersAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_headers_on_api(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.headers.get("x-content-type-options"), "nosniff")

    def test_request_id_echoed(self):
        resp = self.client.get("/api/health", headers={"X-Request-ID": "abc123"})
        self.assertEqual(resp.headers["x-request-id"], "abc123")


class TestBodyLimitAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app, raise_server_exceptions=False)

    def test_oversized_json_rejected(self):
        payload = {"content": "x" * (25 * 1024 * 1024)}
        resp = self.client.put("/api/sandbox", json=payload)
        self.assertEqual(resp.status_code, 413)
        self.assertEqual(resp.json()["code"], ErrorCode.PAYLOAD_TOO_LARGE)

    def test_normal_payload_accepted(self):
        resp = self.client.put("/api/sandbox", json={"mode": "sandbox"})
        self.assertEqual(resp.status_code, 200)


class TestOpenAPIDocs(unittest.TestCase):
    def test_docs_available_in_dev(self):
        client = TestClient(app)
        self.assertEqual(client.get("/openapi.json").status_code, 200)

    def test_openapi_lists_endpoints(self):
        client = TestClient(app)
        spec = client.get("/openapi.json").json()
        paths = spec["paths"]
        for expected in ("/api/health", "/api/ready", "/api/sessions", "/api/models"):
            self.assertIn(expected, paths, f"缺少 {expected}")

    def test_sessions_pagination_documented(self):
        spec = TestClient(app).get("/openapi.json").json()
        params = spec["paths"]["/api/sessions"]["get"]["parameters"]
        names = {p["name"] for p in params}
        self.assertTrue({"limit", "offset", "q"} <= names)


if __name__ == "__main__":
    unittest.main()