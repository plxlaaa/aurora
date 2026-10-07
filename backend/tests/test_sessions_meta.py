"""会话定点查询与路径加固测试。

覆盖：get_meta 定点读取元信息、退化/非法 id 一律拒绝（不得命中共享
`.jsonl` 文件）、导出格式参数校验。
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AI_ASSISTANT_DATA_DIR", tempfile.mkdtemp(prefix="aurora-meta-test-"))
os.environ.setdefault("AI_ASSISTANT_CONFIG_DIR", tempfile.mkdtemp(prefix="aurora-meta-cfg-"))

from app.services.sessions import SessionStore  # noqa: E402


class GetMetaTests(unittest.TestCase):
    """get_meta：只读目标文件的定点查询。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aurora-getmeta-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.store = SessionStore(data_dir=self.dir)

    def test_returns_metadata_for_existing_session(self):
        sid = self.store.create()
        self.store.append(sid, {"role": "user", "content": "首个提问"})
        self.store.append(sid, {"role": "assistant", "content": "回答"})

        meta = self.store.get_meta(sid)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["id"], sid)
        self.assertEqual(meta["message_count"], 2)
        self.assertEqual(meta["title"], "首个提问")

    def test_custom_title_wins_over_preview(self):
        sid = self.store.create()
        self.store.append(sid, {"role": "user", "content": "原始内容"})
        self.store.rename(sid, "自定义标题")
        self.assertEqual(self.store.get_meta(sid)["title"], "自定义标题")

    def test_missing_session_returns_none(self):
        self.assertIsNone(self.store.get_meta("does-not-exist"))

    def test_mtime_not_exposed(self):
        """mtime 是内部排序键，不应泄漏到对外元信息里。"""
        sid = self.store.create()
        self.assertNotIn("mtime", self.store.get_meta(sid))

    def test_empty_session_reports_default_title(self):
        sid = self.store.create()
        self.assertEqual(self.store.get_meta(sid)["title"], "新会话")

    def test_does_not_scan_other_sessions(self):
        """即使存在大量其它会话，定点查询也只读取目标文件。"""
        target = self.store.create()
        self.store.append(target, {"role": "user", "content": "目标会话"})
        for i in range(5):
            other = self.store.create()
            self.store.append(other, {"role": "user", "content": f"其他会话{i}"})

        scanned: list[str] = []
        original = self.store._scan_file

        def spy(path, session_id):
            scanned.append(session_id)
            return original(path, session_id)

        self.store._scan_file = spy  # type: ignore[method-assign]
        meta = self.store.get_meta(target)
        self.assertEqual(meta["id"], target)
        self.assertEqual(scanned, [target], "get_meta 只应读取目标会话文件")


class DegenerateIdTests(unittest.TestCase):
    """非法 id 不得退化为共享 `.jsonl` 文件。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aurora-degen-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.store = SessionStore(data_dir=self.dir)

    def test_get_meta_rejects_degenerate_ids(self):
        for bad in ("..", "../..", "///", "..\\..", "!", "@@"):
            with self.subTest(bad=bad):
                self.assertIsNone(self.store.get_meta(bad))

    def test_exists_rejects_degenerate_ids(self):
        for bad in ("..", "///", "!"):
            with self.subTest(bad=bad):
                self.assertFalse(self.store.exists(bad))

    def test_load_rejects_degenerate_ids(self):
        for bad in ("..", "///", "!"):
            with self.subTest(bad=bad):
                self.assertEqual(self.store.load(bad), [])

    def test_cannot_read_shared_dotfile_via_traversal(self):
        """核心回归：过滤后为空时不得命中 `.jsonl` 这个共享文件。"""
        shared = Path(self.dir) / ".jsonl"
        shared.write_text('{"role": "user", "content": "不该被读到"}\n', encoding="utf-8")
        self.assertEqual(self.store.load(".."), [])
        self.assertIsNone(self.store.get_meta(".."))
        self.assertFalse(self.store.exists(".."))

    def test_valid_ids_still_work(self):
        """加固不得影响正常会话 id 的读写。"""
        sid = self.store.create()
        self.store.append(sid, {"role": "user", "content": "正常"})
        self.assertTrue(self.store.exists(sid))
        self.assertEqual(self.store.load(sid)[0]["content"], "正常")

    def test_dashed_and_underscore_ids_supported(self):
        sid = self.store.create()
        renamed = sid.replace(sid[:4], "ab_c-1")
        self.store.append(renamed, {"role": "user", "content": "带分隔符"})
        self.assertEqual(self.store.load(renamed)[0]["content"], "带分隔符")


if __name__ == "__main__":
    unittest.main()
