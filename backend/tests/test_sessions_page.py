"""会话列表分页与增量缓存测试。

覆盖：分页切片、关键字过滤、总数统计、增量缓存失效（mtime/size 变化）、
新增与删除会话的缓存同步。
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AI_ASSISTANT_DATA_DIR", tempfile.mkdtemp(prefix="aurora-sess-page-"))

from app.services.sessions import SessionStore  # noqa: E402


class TestListSessionsPage(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aurora-page-")
        self.store = SessionStore(data_dir=self.dir)

    def _make(self, count: int, prefix: str = "会话") -> list[str]:
        ids = []
        for i in range(count):
            sid = self.store.create()
            self.store.append(sid, {"role": "user", "content": f"{prefix}{i:02d} 的问题"})
            ids.append(sid)
            # 保证 mtime 递增稳定（部分文件系统精度为秒）
            time.sleep(0.01)
        return ids

    def test_empty_store(self):
        items, total = self.store.list_sessions_page()
        self.assertEqual(items, [])
        self.assertEqual(total, 0)

    def test_returns_all_by_default(self):
        self._make(5)
        items, total = self.store.list_sessions_page()
        self.assertEqual(total, 5)
        self.assertEqual(len(items), 5)

    def test_limit_and_offset(self):
        self._make(10)
        items, total = self.store.list_sessions_page(limit=3, offset=0)
        self.assertEqual(total, 10)
        self.assertEqual(len(items), 3)
        items2, _ = self.store.list_sessions_page(limit=3, offset=3)
        self.assertEqual(len(items2), 3)
        # 两页不应重叠
        self.assertEqual({s["id"] for s in items} & {s["id"] for s in items2}, set())

    def test_offset_beyond_end_returns_empty(self):
        self._make(2)
        items, total = self.store.list_sessions_page(limit=10, offset=100)
        self.assertEqual(total, 2)
        self.assertEqual(items, [])

    def test_keyword_filter(self):
        self._make(3, prefix="天气")
        self.store.append(self.store.create(), {"role": "user", "content": "写一首诗"})
        items, total = self.store.list_sessions_page(keyword="天气")
        self.assertEqual(total, 3)
        self.assertTrue(all("天气" in s["title"] for s in items))

    def test_keyword_no_match(self):
        self._make(2)
        items, total = self.store.list_sessions_page(keyword="不存在的关键字")
        self.assertEqual(total, 0)
        self.assertEqual(items, [])

    def test_keyword_is_case_insensitive(self):
        sid = self.store.create()
        self.store.rename(sid, "Python 笔记")
        items, total = self.store.list_sessions_page(keyword="python")
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["id"], sid)

    def test_sorted_by_mtime_desc(self):
        self._make(4)
        items, _ = self.store.list_sessions_page()
        mtimes = [s["updated_at"] for s in items]
        self.assertEqual(mtimes, sorted(mtimes, reverse=True))

    def test_legacy_signature_still_works(self):
        self._make(2)
        self.assertIsInstance(self.store.list_sessions(), list)

    def test_mtime_not_leaked_in_page(self):
        self._make(1)
        items, _ = self.store.list_sessions_page()
        self.assertNotIn("mtime", items[0])


class TestIncrementalCache(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aurora-cache-")
        self.store = SessionStore(data_dir=self.dir)

    def test_new_session_appears_without_reset(self):
        first = self.store.create()
        self.store.list_sessions_page()
        second = self.store.create()
        ids = [s["id"] for s in self.store.list_sessions_page()[0]]
        self.assertIn(first, ids)
        self.assertIn(second, ids)

    def test_deleted_session_disappears(self):
        sid = self.store.create()
        self.store.list_sessions_page()
        self.assertTrue(self.store.delete(sid))
        self.assertNotIn(sid, [s["id"] for s in self.store.list_sessions_page()[0]])

    def test_content_change_refreshes_count(self):
        sid = self.store.create()
        self.store.append(sid, {"role": "user", "content": "第一条"})
        items, _ = self.store.list_sessions_page()
        entry = next(s for s in items if s["id"] == sid)
        self.assertEqual(entry["message_count"], 1)
        self.assertIn("第一条", entry["title"])
        # 追加后应刷新（签名变化）
        self.store.append(sid, {"role": "assistant", "content": "回复"})
        items, _ = self.store.list_sessions_page()
        entry = next(s for s in items if s["id"] == sid)
        self.assertEqual(entry["message_count"], 2)

    def test_rename_updates_title(self):
        sid = self.store.create()
        self.store.append(sid, {"role": "user", "content": "原始标题"})
        items, _ = self.store.list_sessions_page()
        self.assertEqual(next(s for s in items if s["id"] == sid)["title"], "原始标题")
        self.store.rename(sid, "新标题")
        items, _ = self.store.list_sessions_page()
        self.assertEqual(next(s for s in items if s["id"] == sid)["title"], "新标题")

    def test_archive_removes_from_list(self):
        sid = self.store.create()
        self.store.append(sid, {"role": "user", "content": "待归档"})
        self.store.list_sessions_page()
        self.store.archive(sid)
        self.assertNotIn(sid, [s["id"] for s in self.store.list_sessions_page()[0]])

    def test_delta_messages_not_counted(self):
        sid = self.store.create()
        self.store.append(sid, {"role": "user", "content": "问题"})
        self.store.append(sid, {"role": "assistant_delta", "content": "片段"})
        items, _ = self.store.list_sessions_page()
        entry = next(s for s in items if s["id"] == sid)
        self.assertEqual(entry["message_count"], 1)

    def test_cache_reuses_entry_when_unchanged(self):
        sid = self.store.create()
        self.store.append(sid, {"role": "user", "content": "内容"})
        self.store.list_sessions_page()
        cached = self.store._session_cache.get(sid)
        # 再次读取不应重新解析文件（缓存对象保持同一内容与 mtime 签名）
        items, _ = self.store.list_sessions_page()
        self.assertEqual(self.store._session_cache.get(sid), cached)
        self.assertEqual(len(items), 1)

    def test_corrupt_line_skipped(self):
        sid = self.store.create()
        path = self.store._path(sid)
        path.write_text('{"role": "user", "content": "好的"}\n坏行\n', encoding="utf-8")
        items, _ = self.store.list_sessions_page()
        entry = next(s for s in items if s["id"] == sid)
        self.assertEqual(entry["message_count"], 1)
        self.assertEqual(entry["title"], "好的")


if __name__ == "__main__":
    unittest.main()