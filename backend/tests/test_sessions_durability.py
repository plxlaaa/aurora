"""会话持久化健壮性测试（v1.3.0）。

覆盖三类改进：
1. **原子写**：会话文件、状态表与标题表均采用「临时文件 + os.replace」，
   写入过程崩溃不会留下截断文件（原先直接覆写目标文件）。
2. **孤儿清理**：会话文件被外部删除后，状态/标题表中的残留条目会被回收，
   避免两张表随运行时间无界增长。
3. **write失败不抛**：状态/标题写盘失败仅记日志，不中断主流程。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.sessions import SessionStore  # noqa: E402


def new_store() -> tuple[SessionStore, str]:
    """在独立临时目录上创建 SessionStore。"""
    path = tempfile.mkdtemp(prefix="aurora-sess-test-")
    return SessionStore(data_dir=path), path


class TestAtomicWrite(unittest.TestCase):
    """原子替换：任何时刻磁盘上的会话文件都是完整 JSONL。"""

    def test_save_leaves_no_temp_files(self):
        store, path = new_store()
        sid = store.create()
        store.save(sid, [{"role": "user", "content": "hello"}])
        leftovers = [f for f in os.listdir(path) if f.endswith(".tmp") or f.startswith(".")]
        self.assertEqual(leftovers, [], f"残留临时文件: {leftovers}")

    def test_content_written_correctly(self):
        store, _ = new_store()
        sid = store.create()
        messages = [
            {"role": "user", "content": "第一条"},
            {"role": "assistant", "content": "第二条"},
        ]
        store.save(sid, messages)
        self.assertEqual(store.load(sid), messages)

    def test_collapse_deltas_leaves_no_temp_files(self):
        store, path = new_store()
        sid = store.create()
        store.save(sid, [
            {"role": "user", "content": "q"},
            {"role": "assistant_delta", "content": "a1"},
            {"role": "assistant_delta", "content": "a2"},
        ])
        store.collapse_deltas(sid)
        self.assertEqual([f for f in os.listdir(path) if f.endswith(".tmp")], [])
        self.assertEqual(store.load(sid)[-1]["content"], "a1a2")

    def test_failed_write_keeps_original_intact(self):
        """写入过程抛异常时，原文件内容必须保持不变。"""
        store, _ = new_store()
        sid = store.create()
        store.save(sid, [{"role": "user", "content": "原始内容"}])

        # 让 json.dumps 在中途抛错，模拟写盘中断
        with mock.patch(
            "app.services.sessions.json.dumps", side_effect=RuntimeError("模拟写入中断")
        ):
            with self.assertRaises(RuntimeError):
                store.save(sid, [{"role": "user", "content": "新内容"}])

        # 原文件应完好无损
        self.assertEqual(store.load(sid), [{"role": "user", "content": "原始内容"}])

    def test_failed_write_removes_temp_file(self):
        store, path = new_store()
        sid = store.create()
        store.save(sid, [{"role": "user", "content": "原始"}])
        with mock.patch(
            "app.services.sessions.json.dumps", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                store.save(sid, [{"role": "user", "content": "新"}])
        leftovers = [f for f in os.listdir(path) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [], f"异常后残留临时文件: {leftovers}")


class TestStatusAndTitlesPersistence(unittest.TestCase):
    """状态表/标题表的原子写与容错。"""

    def test_status_roundtrip(self):
        store, _ = new_store()
        sid = store.create()
        store.mark_completed(sid)
        reloaded = SessionStore(data_dir=store.data_dir)
        self.assertEqual(reloaded.get_status(sid), SessionStore.STATUS_COMPLETED)

    def test_running_state_reset_on_reload(self):
        """重启后残留的 running 状态应重置为 idle（进程重启不可能仍在生成）。"""
        store, _ = new_store()
        sid = store.create()
        store.mark_running(sid)
        reloaded = SessionStore(data_dir=store.data_dir)
        self.assertEqual(reloaded.get_status(sid), SessionStore.STATUS_IDLE)

    def test_titles_roundtrip(self):
        store, _ = new_store()
        sid = store.create()
        store.rename(sid, "我的会话")
        reloaded = SessionStore(data_dir=store.data_dir)
        self.assertEqual(reloaded._titles.get(sid), "我的会话")

    def test_status_write_failure_does_not_raise(self):
        """状态落盘失败不应中断调用方（辅助数据）。"""
        store, _ = new_store()
        sid = store.create()
        with mock.patch("os.replace", side_effect=OSError("磁盘只读")):
            store.mark_running(sid)  # 不应抛异常
        self.assertEqual(store.get_status(sid), SessionStore.STATUS_RUNNING)

    def test_titles_write_failure_does_not_raise(self):
        store, _ = new_store()
        sid = store.create()
        with mock.patch("os.replace", side_effect=OSError("磁盘只读")):
            store.rename(sid, "标题")  # 不应抛异常
        self.assertEqual(store._titles.get(sid), "标题")

    def test_status_file_not_truncated_on_failure(self):
        """写失败不应破坏既有状态文件。"""
        store, path = new_store()
        sid = store.create()
        store.mark_completed(sid)
        status_file = os.path.join(str(store.data_dir), "_status.json")
        with open(status_file, encoding="utf-8") as fh:
            before = json.load(fh)
        with mock.patch("os.replace", side_effect=OSError("失败")):
            store.mark_running(sid)
        with open(status_file, encoding="utf-8") as fh:
            after = json.load(fh)
        self.assertEqual(before, after, "状态文件在写失败后被破坏")


class TestOrphanCleanup(unittest.TestCase):
    """孤儿状态/标题条目回收。"""

    def test_orphan_entries_pruned(self):
        store, _ = new_store()
        sid = store.create()
        store.rename(sid, "标题")
        store.mark_completed(sid)
        self.assertIn(sid, store._status)
        self.assertIn(sid, store._titles)

        # 模拟会话文件被外部删除（绕过 delete()）
        os.unlink(os.path.join(str(store.data_dir), f"{sid}.jsonl"))

        store.list_sessions_page()
        self.assertNotIn(sid, store._status, "孤儿状态条目未被清理")
        self.assertNotIn(sid, store._titles, "孤儿标题条目未被清理")

    def test_live_entries_preserved(self):
        store, _ = new_store()
        keep = store.create()
        drop = store.create()
        store.rename(keep, "保留")
        store.rename(drop, "删除")
        os.unlink(os.path.join(str(store.data_dir), f"{drop}.jsonl"))

        store.list_sessions_page()
        self.assertIn(keep, store._status)
        self.assertIn(keep, store._titles)
        self.assertEqual(store._titles.get(keep), "保留")

    def test_running_session_not_pruned(self):
        """生成中的会话文件存在，不应被当作孤儿清理。"""
        store, _ = new_store()
        sid = store.create()
        store.mark_running(sid)
        store.list_sessions_page()
        self.assertEqual(store.get_status(sid), SessionStore.STATUS_RUNNING)


class TestLoadCache(unittest.TestCase):
    """内容读缓存：sig 命中零读盘、文件变化即失效、返回浅拷贝防污染。"""

    def test_cache_hit_and_invalidate(self):
        store, _ = new_store()
        sid = store.create()
        store.append(sid, {"role": "user", "content": "hi"})
        first = store.load(sid)
        # 命中：修改缓存内部列表不应影响后续 load（浅拷贝防御）
        first.append({"role": "user", "content": "polluted"})
        second = store.load(sid)
        self.assertEqual(len(second), 1)

    def test_cache_invalidated_by_write(self):
        store, _ = new_store()
        sid = store.create()
        store.append(sid, {"role": "user", "content": "a"})
        self.assertEqual(len(store.load(sid)), 1)
        store.append(sid, {"role": "assistant", "content": "b"})
        # append 后 sig 变化，load 必须看到新消息
        self.assertEqual(len(store.load(sid)), 2)

    def test_cache_invalidated_by_save(self):
        store, _ = new_store()
        sid = store.create()
        store.append(sid, {"role": "user", "content": "a"})
        history = store.load(sid)
        del history[:]
        store.save(sid, history)
        self.assertEqual(store.load(sid), [])

    def test_cache_no_leak_after_delete(self):
        store, _ = new_store()
        sid = store.create()
        store.append(sid, {"role": "user", "content": "a"})
        store.load(sid)
        store.delete(sid)
        self.assertNotIn(sid, store._load_cache)
        self.assertEqual(store.load(sid), [])


if __name__ == "__main__":
    unittest.main()
