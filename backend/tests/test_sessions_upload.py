"""会话存储与附件上传单元测试。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import upload as upload_mod  # noqa: E402
from app.services.sessions import SessionStore  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class TestSessionStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_is_visible_immediately(self):
        sid = self.store.create()
        self.assertTrue(self.store.exists(sid))
        self.assertIn(sid, [s["id"] for s in self.store.list_sessions()])

    def test_append_stream_merges_same_role(self):
        sid = self.store.create()
        for piece in ("你", "好", "呀"):
            self.store.append_stream(sid, "assistant_delta", piece)
        messages = self.store.load(sid)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["content"], "你好呀")

    def test_collapse_deltas_produces_single_assistant(self):
        sid = self.store.create()
        self.store.append(sid, {"role": "user", "content": "问题"})
        self.store.append(sid, {"role": "assistant_delta", "content": "答"})
        self.store.append(sid, {"role": "assistant_delta", "content": "案"})
        self.store.collapse_deltas(sid)
        messages = self.store.load(sid)
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[1]["content"], "答案")
        self.assertNotIn("_from_delta", messages[1])

    def test_message_count_skips_deltas(self):
        sid = self.store.create()
        self.store.append(sid, {"role": "user", "content": "hi"})
        self.store.append(sid, {"role": "assistant_delta", "content": "a"})
        self.store.append(sid, {"role": "assistant_delta", "content": "b"})
        entry = next(s for s in self.store.list_sessions() if s["id"] == sid)
        self.assertEqual(entry["message_count"], 1)
        self.assertEqual(entry["title"], "hi")

    def test_list_sorted_by_update_time(self):
        import os

        first = self.store.create()
        second = self.store.create()
        # 显式设置不同 mtime，避免同一时钟刻度内创建导致排序不稳定
        os.utime(self.store._path(first), (2000, 2000))
        os.utime(self.store._path(second), (1000, 1000))
        self.store.append(first, {"role": "user", "content": "later"})
        ids = [s["id"] for s in self.store.list_sessions()]
        self.assertEqual(ids[0], first)
        self.assertIn(second, ids)

    def test_clear_keeps_session_delete_removes_it(self):
        sid = self.store.create()
        self.store.append(sid, {"role": "user", "content": "x"})
        self.store.clear(sid)
        self.assertTrue(self.store.exists(sid))
        self.assertEqual(self.store.load(sid), [])
        self.assertTrue(self.store.delete(sid))
        self.assertFalse(self.store.exists(sid))
        self.assertFalse(self.store.delete(sid))

    def test_rename_missing_session_raises(self):
        with self.assertRaises(FileNotFoundError):
            self.store.rename("nope", "标题")

    def test_running_status_reset_on_restart(self):
        sid = self.store.create()
        self.store.mark_running(sid)
        reopened = SessionStore(self._tmp.name)
        self.assertEqual(reopened.get_status(sid), SessionStore.STATUS_IDLE)


class TestUpload(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_dir = upload_mod.UPLOAD_DIR
        upload_mod.UPLOAD_DIR = self._tmp.name

    def tearDown(self):
        upload_mod.UPLOAD_DIR = self._orig_dir
        self._tmp.cleanup()

    def test_sniff_overrides_wrong_content_type(self):
        info = upload_mod.save_upload("a.bin", "application/octet-stream", PNG)
        self.assertEqual(info["mime_type"], "image/png")
        self.assertEqual(info["size"], len(PNG))

    def test_reject_empty_and_unknown(self):
        with self.assertRaises(upload_mod.UploadError):
            upload_mod.save_upload("a.png", "image/png", b"")
        with self.assertRaises(upload_mod.UploadError):
            upload_mod.save_upload("a.txt", "text/plain", b"hello world")

    def test_load_object_with_mime(self):
        info = upload_mod.save_upload("a.png", "image/png", PNG)
        found = upload_mod.load_object_with_mime(info["object_id"])
        self.assertIsNotNone(found)
        data, mime = found
        self.assertEqual(data, PNG)
        self.assertEqual(mime, "image/png")
        self.assertIsNone(upload_mod.load_object_with_mime("missing"))

    def test_webp_sniff(self):
        webp = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"0" * 16
        self.assertEqual(upload_mod.sniff_mime(webp), "image/webp")


if __name__ == "__main__":
    unittest.main()
