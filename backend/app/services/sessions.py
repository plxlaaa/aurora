"""会话管理与持久化。

以 JSONL 文件按会话持久化消息历史，提供读取/追加/清空/计数接口。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
import datetime as dt
from pathlib import Path
from typing import Any

logger = logging.getLogger("aiassistant.sessions")

DATA_DIR = os.environ.get("AI_ASSISTANT_DATA_DIR", "/opt/aiassistant/data/sessions")


class SessionStore:
    STATUS_IDLE = "idle"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"

    def __init__(self, data_dir: str = DATA_DIR) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # 会话运行状态：{session_id: status}
        self._status: dict[str, str] = {}
        self._status_path = self.data_dir / "_status.json"
        self._load_status()
        # 自定义会话标题：{session_id: title}
        self._titles: dict[str, str] = {}
        self._titles_path = self.data_dir / "_titles.json"
        self._load_titles()

    def _load_status(self) -> None:
        if self._status_path.exists():
            try:
                with open(self._status_path, "r", encoding="utf-8") as fh:
                    self._status = json.load(fh)
            except Exception:
                self._status = {}
        # 进程重启后不可能仍有会话在生成中，重置残留的 running 状态
        stale = [sid for sid, st in self._status.items() if st == self.STATUS_RUNNING]
        for sid in stale:
            self._status[sid] = self.STATUS_IDLE
        if stale:
            self._save_status()

    def _save_status(self) -> None:
        try:
            with open(self._status_path, "w", encoding="utf-8") as fh:
                json.dump(self._status, fh, ensure_ascii=False)
        except Exception:
            pass

    def _load_titles(self) -> None:
        if self._titles_path.exists():
            try:
                with open(self._titles_path, "r", encoding="utf-8") as fh:
                    self._titles = json.load(fh)
            except Exception:
                self._titles = {}

    def _save_titles(self) -> None:
        try:
            with open(self._titles_path, "w", encoding="utf-8") as fh:
                json.dump(self._titles, fh, ensure_ascii=False)
        except Exception:
            pass

    def rename(self, session_id: str, title: str) -> None:
        """设置自定义会话标题；传空串则清除自定义标题。"""
        with self._lock:
            if not self._path(session_id).exists():
                raise FileNotFoundError(session_id)
            if title and title.strip():
                self._titles[session_id] = title.strip()[:80]
            else:
                self._titles.pop(session_id, None)
            self._save_titles()

    def set_status(self, session_id: str, status: str) -> None:
        with self._lock:
            self._status[session_id] = status
            self._save_status()

    def get_status(self, session_id: str) -> str:
        with self._lock:
            return self._status.get(session_id, self.STATUS_IDLE)

    def mark_running(self, session_id: str) -> None:
        self.set_status(session_id, self.STATUS_RUNNING)

    def mark_completed(self, session_id: str) -> None:
        self.set_status(session_id, self.STATUS_COMPLETED)

    def _path(self, session_id: str) -> Path:
        # 仅允许由我们生成的 session id 形态，防路径穿越
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        return self.data_dir / f"{safe}.jsonl"

    def create(self) -> str:
        """创建会话并立即落盘空文件，确保新会话在列表中可见。"""
        sid = uuid.uuid4().hex[:24]
        with self._lock:
            self._path(sid).touch(exist_ok=True)
            self._status[sid] = self.STATUS_IDLE
            self._save_status()
        return sid

    def exists(self, session_id: str) -> bool:
        with self._lock:
            return self._path(session_id).exists()

    def append(self, session_id: str, message: dict[str, Any]) -> None:
        with self._lock:
            path = self._path(session_id)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(message, ensure_ascii=False) + "\n")

    def append_stream(self, session_id: str, role: str, text: str) -> None:
        """追加流式增量：合并进末尾的同 role 记录，避免 JSONL 行数爆炸。"""
        if not text:
            return
        with self._lock:
            path = self._path(session_id)
            messages = self.load(session_id)
            if messages and messages[-1].get("role") == role:
                messages[-1]["content"] = (messages[-1].get("content") or "") + text
                self.save(session_id, messages)
                return
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps({"role": role, "content": text}, ensure_ascii=False) + "\n"
                )

    def collapse_deltas(self, session_id: str) -> None:
        """一轮生成结束后，将连续的 assistant_delta 合并为单条 assistant 消息。"""
        with self._lock:
            messages = self.load(session_id)
            if not any(m.get("role") == "assistant_delta" for m in messages):
                return
            merged: list[dict[str, Any]] = []
            for msg in messages:
                if msg.get("role") != "assistant_delta":
                    merged.append(msg)
                    continue
                text = msg.get("content") or ""
                if merged and merged[-1].get("role") == "assistant" and merged[-1].get("_from_delta"):
                    merged[-1]["content"] = (merged[-1].get("content") or "") + text
                else:
                    merged.append({"role": "assistant", "content": text, "_from_delta": True})
            for msg in merged:
                msg.pop("_from_delta", None)
            self.save(session_id, merged)

    def load(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            path = self._path(session_id)
            if not path.exists():
                return []
            messages: list[dict[str, Any]] = []
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            return messages

    def clear(self, session_id: str) -> None:
        """清空会话内容但保留会话本身（文件截断为空）。"""
        with self._lock:
            path = self._path(session_id)
            if path.exists():
                path.write_text("", encoding="utf-8")

    def delete(self, session_id: str) -> bool:
        """彻底删除会话文件与附属状态；返回会话此前是否存在。"""
        with self._lock:
            path = self._path(session_id)
            existed = path.exists()
            if existed:
                path.unlink()
            self._status.pop(path.stem, None)
            self._titles.pop(path.stem, None)
            self._save_status()
            self._save_titles()
            return existed

    def save(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        with self._lock:
            path = self._path(session_id)
            with open(path, "w", encoding="utf-8") as fh:
                for msg in messages:
                    fh.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def archive(self, session_id: str) -> Path | None:
        """将会话文件移动到 archive 子目录，返回目标路径；会话不存在时返回 None。"""
        with self._lock:
            path = self._path(session_id)
            if not path.exists():
                return None
            archive_dir = self.data_dir / "archive"
            archive_dir.mkdir(exist_ok=True)
            dest = archive_dir / path.name
            path.replace(dest)
            self._status.pop(path.stem, None)
            self._save_status()
            return dest

    def list_sessions(self) -> list[dict[str, Any]]:
        """返回会话列表，按最后更新时间倒序（最新在前）。"""
        with self._lock:
            sessions = []
            for path in self.data_dir.glob("*.jsonl"):
                # 一次遍历同时提取首条 user 消息标题与可见消息计数
                preview = ""
                count = 0
                try:
                    with open(path, encoding="utf-8") as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                msg = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            role = msg.get("role")
                            # assistant_delta 为流式碎片，不计入消息数
                            if role != "assistant_delta":
                                count += 1
                            if not preview and role == "user":
                                content = msg.get("content", "")
                                if isinstance(content, str) and content.strip():
                                    preview = content.strip()[:50]
                except OSError:
                    continue
                stat = path.stat()
                sessions.append(
                    {
                        "id": path.stem,
                        "title": self._titles.get(path.stem) or preview or "新会话",
                        "message_count": count,
                        "status": self.get_status(path.stem),
                        "created_at": dt.datetime.fromtimestamp(stat.st_ctime, tz=dt.timezone.utc).isoformat(),
                        "updated_at": dt.datetime.fromtimestamp(stat.st_mtime, tz=dt.timezone.utc).isoformat(),
                        "mtime": stat.st_mtime,
                    }
                )
            sessions.sort(key=lambda s: s.pop("mtime"), reverse=True)
            return sessions
