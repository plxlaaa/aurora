"""会话导出接口测试（TestClient + 真实 app）。

覆盖：md / json 两种格式导出、format 参数校验（原先非法值会被静默
当作 md 处理）、不存在会话返回 404。
"""
import json as _json
import os
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AI_ASSISTANT_DATA_DIR", tempfile.mkdtemp(prefix="aurora-export-test-"))
os.environ.setdefault("AI_ASSISTANT_CONFIG_DIR", tempfile.mkdtemp(prefix="aurora-export-cfg-"))
os.environ.setdefault("AI_ASSISTANT_API_KEY", "")  # 默认关闭鉴权

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.routers import deps  # noqa: E402


class TestSessionExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _seed(self) -> str:
        sid = deps.sessions.create()
        deps.sessions.save(sid, [
            {"role": "user", "content": "导出验证问题"},
            {"role": "assistant", "content": "导出验证回答"},
        ])
        return sid

    def test_export_markdown(self):
        sid = self._seed()
        resp = self.client.get(f"/api/sessions/{sid}/export?format=md")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/markdown", resp.headers["content-type"])
        self.assertIn("# 导出验证问题", resp.text)
        self.assertIn("导出验证回答", resp.text)

    def test_export_defaults_to_markdown(self):
        sid = self._seed()
        resp = self.client.get(f"/api/sessions/{sid}/export")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/markdown", resp.headers["content-type"])

    def test_export_json(self):
        sid = self._seed()
        resp = self.client.get(f"/api/sessions/{sid}/export?format=json")
        self.assertEqual(resp.status_code, 200)
        data = _json.loads(resp.text)
        self.assertEqual(data["id"], sid)
        self.assertEqual(data["title"], "导出验证问题")
        self.assertEqual(len(data["messages"]), 2)

    def test_export_uses_custom_title(self):
        """导出标题应取自定义标题（验证 get_meta 接入生效）。"""
        sid = self._seed()
        self.client.post(f"/api/sessions/{sid}/rename", json={"title": "我的重要会话"})
        resp = self.client.get(f"/api/sessions/{sid}/export?format=json")
        self.assertEqual(_json.loads(resp.text)["title"], "我的重要会话")

    def test_export_rejects_unknown_format(self):
        """非法 format 应返回 400 而非静默降级为 markdown。"""
        sid = self._seed()
        resp = self.client.get(f"/api/sessions/{sid}/export?format=xml")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "validation_error")

    def test_export_missing_session_returns_404(self):
        resp = self.client.get("/api/sessions/does-not-exist/export?format=md")
        self.assertEqual(resp.status_code, 404)

    def test_export_filename_encoded(self):
        """中文标题的 Content-Disposition 需含 RFC 5987 的 filename*。"""
        sid = self._seed()
        self.client.post(f"/api/sessions/{sid}/rename", json={"title": "中文标题测试"})
        resp = self.client.get(f"/api/sessions/{sid}/export?format=md")
        disp = resp.headers.get("content-disposition", "")
        self.assertIn("filename*=UTF-8''", disp)
        encoded = disp.split("filename*=UTF-8''")[1].split(";")[0].split(".")[0]
        self.assertEqual(urllib.parse.unquote(encoded), "中文标题测试")


if __name__ == "__main__":
    unittest.main()
