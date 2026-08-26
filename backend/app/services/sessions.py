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
        # Track active session status: {session_id: status}
        self._status: dict[str, str] = {}
        self._status_path = self.data_dir / "_status.json"
        self._load_status()
        # Custom session titles: {session_id: title}
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
        sid = uuid.uuid4().hex[:24]
        return sid

    def append(self, session_id: str, message: dict[str, Any]) -> None:
        with self._lock:
            path = self._path(session_id)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(message, ensure_ascii=False) + "\n")

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
        with self._lock:
            path = self._path(session_id)
            if path.exists():
                path.unlink()

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
            return dest

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            sessions = []
            for path in sorted(self.data_dir.glob("*.jsonl")):
                # 一次遍历同时提取首条 user 消息标题与消息计数
                title = path.stem
                preview = ""
                count = 0
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        count += 1
                        if not preview:
                            try:
                                msg = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if msg.get("role") == "user":
                                content = msg.get("content", "")
                                if isinstance(content, str) and content.strip():
                                    preview = content.strip()[:50]
                stat = path.stat()
                sessions.append(
                    {
                        "id": path.stem,
                        "title": self._titles.get(path.stem) or preview or title,
                        "message_count": count,
                        "status": self.get_status(path.stem),
                        "created_at": dt.datetime.fromtimestamp(stat.st_ctime, tz=dt.timezone.utc).isoformat(),
                        "updated_at": dt.datetime.fromtimestamp(stat.st_mtime, tz=dt.timezone.utc).isoformat(),
                    }
                )
            return sessions
