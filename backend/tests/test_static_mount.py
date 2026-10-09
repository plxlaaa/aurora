"""前端静态托管安全与行为测试（v1.3.0 修复路径穿越）。

背景：`_mount_frontend` 原先用 `str(candidate).startswith(str(root))` 做边界判断。
字符串前缀匹配不区分路径分量，`dist` 会误匹配同级兄弟目录 `dist-secrets/`，
配合 URL 编码（`%2F` / `%2e%2e`）即可读取 dist 目录之外的文件。

本测试直接在 ASGI 层构造带前端目录的应用，验证：
1. 目录内文件正常返回；
2. 各类穿越尝试（相对、URL 编码、同级兄弟目录、系统文件）均不泄漏内容；
3. SPA history 模式回退与 /api 前缀 404 语义保持不变。
"""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AI_ASSISTANT_DATA_DIR", tempfile.mkdtemp(prefix="aurora-static-test-"))
os.environ.setdefault("AI_ASSISTANT_CONFIG_DIR", tempfile.mkdtemp(prefix="aurora-static-cfg-"))

INDEX_HTML = "<html>INDEX-PAGE</html>"


def build_static_app(dist_dir: str):
    """以指定前端目录构建应用（每次调用都重新导入 main以读取新环境变量）。"""
    os.environ["AI_ASSISTANT_FRONTEND_DIR"] = dist_dir
    import app.core.runtime as runtime_mod

    importlib.reload(runtime_mod)
    import app.main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


class StaticMountTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="aurora-static-root-")
        cls.dist = os.path.join(cls.base, "dist")
        os.makedirs(os.path.join(cls.dist, "assets"))
        os.makedirs(os.path.join(cls.dist, "sub"))
        with open(os.path.join(cls.dist, "index.html"), "w", encoding="utf-8") as fh:
            fh.write(INDEX_HTML)
        with open(os.path.join(cls.dist, "assets", "app.js"), "w", encoding="utf-8") as fh:
            fh.write("console.log('asset');")
        with open(os.path.join(cls.dist, "sub", "inner.txt"), "w", encoding="utf-8") as fh:
            fh.write("INNER-FILE")
        # 同级兄弟目录：名字以 dist 为前缀，用于验证前缀匹配的经典绕过
        secret_dir = os.path.join(cls.base, "dist-secrets")
        os.makedirs(secret_dir)
        with open(os.path.join(secret_dir, "secret.txt"), "w", encoding="utf-8") as fh:
            fh.write("TOP-SECRET-CONTENT")
        # dist 之外的普通文件
        with open(os.path.join(cls.base, "outside.txt"), "w", encoding="utf-8") as fh:
            fh.write("OUTSIDE-SECRET")

        from fastapi.testclient import TestClient

        cls.client_context = TestClient(build_static_app(cls.dist))
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        os.environ.pop("AI_ASSISTANT_FRONTEND_DIR", None)


class TestStaticMountServes(StaticMountTestBase):
    """目录内资源必须正常可读（防止修复过度收紧）。"""

    def test_index_html(self):
        resp = self.client.get("/index.html")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("INDEX-PAGE", resp.text)

    def test_nested_asset(self):
        resp = self.client.get("/assets/app.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("asset", resp.text)

    def test_subdirectory_file(self):
        resp = self.client.get("/sub/inner.txt")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("INNER-FILE", resp.text)

    def test_root_path_falls_back_to_index(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("INDEX-PAGE", resp.text)

    def test_spa_history_fallback(self):
        """Vue Router history 模式的深链应回退 index.html。"""
        resp = self.client.get("/chat/some/deep/link")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("INDEX-PAGE", resp.text)

    def test_api_prefix_returns_json_404(self):
        """api/ 与 ws/ 前缀不得回退到 index.html。"""
        for path in ("/api/definitely-missing", "/ws/x"):
            with self.subTest(path=path):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 404)
                self.assertNotIn("INDEX-PAGE", resp.text)


class TestStaticMountPathTraversal(StaticMountTestBase):
    """穿越尝试一律不得泄漏 dist 目录外内容。"""

    def assertNoLeak(self, path: str):
        resp = self.client.get(path)
        self.assertNotIn("TOP-SECRET-CONTENT", resp.text, f"泄漏兄弟目录内容: {path}")
        self.assertNotIn("OUTSIDE-SECRET", resp.text, f"泄漏外部文件内容: {path}")
        self.assertNotIn("root:", resp.text, f"泄漏系统文件内容: {path}")

    def test_sibling_directory_prefix_trick(self):
        """dist-secrets 以 dist 为前缀，前缀匹配会被绕过。"""
        self.assertNoLeak("/../dist-secrets/secret.txt")

    def test_url_encoded_traversal(self):
        self.assertNoLeak("/..%2Fdist-secrets%2Fsecret.txt")

    def test_url_encoded_dots(self):
        self.assertNoLeak("/%2e%2e%2fdist-secrets%2fsecret.txt")

    def test_double_encoded_traversal(self):
        self.assertNoLeak("/%252e%252e%252fdist-secrets%252fsecret.txt")

    def test_traversal_to_system_file(self):
        self.assertNoLeak("/..%2F..%2Fetc%2Fpasswd")

    def test_traversal_from_subdirectory(self):
        self.assertNoLeak("/sub/../../dist-secrets/secret.txt")

    def test_four_dot_bypass(self):
        self.assertNoLeak("/....//dist-secrets/secret.txt")

    def test_backslash_traversal(self):
        self.assertNoLeak(r"/..\dist-secrets\secret.txt")

    def test_traversal_still_serves_index_on_rejection(self):
        """越界请求回退 index.html 而不是报错（保持 SPA 体验）。"""
        resp = self.client.get("/..%2Fdist-secrets%2Fsecret.txt")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("INDEX-PAGE", resp.text)


class TestStaticMountEdgeCases(unittest.TestCase):
    """未配置 / 目录不存在时的降级行为。"""

    def test_missing_frontend_dir_is_ignored(self):
        os.environ["AI_ASSISTANT_FRONTEND_DIR"] = ""
        import app.core.runtime as runtime_mod

        importlib.reload(runtime_mod)
        import app.main as main_mod

        importlib.reload(main_mod)
        from fastapi.testclient import TestClient

        with TestClient(main_mod.app) as client:
            resp = client.get("/api/health")
            self.assertEqual(resp.status_code, 200)
        os.environ.pop("AI_ASSISTANT_FRONTEND_DIR", None)

    def test_nonexistent_dir_is_ignored(self):
        os.environ["AI_ASSISTANT_FRONTEND_DIR"] = "/nonexistent/aurora/dist"
        import app.core.runtime as runtime_mod

        importlib.reload(runtime_mod)
        import app.main as main_mod

        importlib.reload(main_mod)
        from fastapi.testclient import TestClient

        with TestClient(main_mod.app) as client:
            resp = client.get("/api/health")
            self.assertEqual(resp.status_code, 200)
        os.environ.pop("AI_ASSISTANT_FRONTEND_DIR", None)


if __name__ == "__main__":
    unittest.main()
