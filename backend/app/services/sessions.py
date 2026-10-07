"""会话管理与持久化。

以 JSONL 文件按会话持久化消息历史，提供读取/追加/清空/计数接口。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
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
        self._pending_delta: dict[str, dict[str, Any]] = {}
        self._load_status()
        # 自定义会话标题：{session_id: title}
        self._titles: dict[str, str] = {}
        self._titles_path = self.data_dir / "_titles.json"
        self._load_titles()
        # 会话列表缓存：{session_id: session_info}，在会话变更时失效
        self._session_cache: dict[str, dict[str, Any]] = {}
        self._cache_valid = False
        # 每个会话文件的签名 (mtime_ns, size)，用于列表缓存的增量失效判断
        self._file_sigs: dict[str, tuple[int, int]] = {}
        # 标题表签名：改名会影响列表展示，改名时整体重建
        self._titles_sig: tuple = ()

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

    def has_custom_title(self, session_id: str) -> bool:
        """用户是否已自定义标题（自动命名等插件据此决定是否介入）。"""
        with self._lock:
            return session_id in self._titles

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
            self._cache_valid = False

    def set_status(self, session_id: str, status: str) -> None:
        with self._lock:
            self._status[session_id] = status
            self._save_status()

    def get_status(self, session_id: str) -> str:
        with self._lock:
            return self._status.get(session_id, self.STATUS_IDLE)

    def get_meta(self, session_id: str) -> dict[str, Any] | None:
        """定点查询单个会话的列表元信息（不含 mtime）；不存在返回 None。

        供会话导出等只需单个会话元信息的场景使用：相比 `list_sessions()`
        遍历全量会话逐个匹配，这里只读取目标文件，开销与会话总数无关。
        """
        with self._lock:
            safe = self._safe_name(session_id)
            if not safe:
                return None
            entry = self._scan_file(self.data_dir / f"{safe}.jsonl", safe)
            if entry is None:
                return None
            entry.pop("mtime", None)
            return entry

    def mark_running(self, session_id: str) -> None:
        self.set_status(session_id, self.STATUS_RUNNING)

    def mark_completed(self, session_id: str) -> None:
        self.set_status(session_id, self.STATUS_COMPLETED)

    @staticmethod
    def _safe_name(session_id: str) -> str:
        """将外部传入的 id 收敛为安全文件名主干；空结果表示非法。

        仅保留字母数字与 -_（与既有会话 id 形态一致），杜绝路径穿越。
        过滤后为空（例如 ".." 或 "///"）时返回空串，由调用方拒绝，
        避免退化成 ".jsonl" 这类共享文件造成会话互相覆盖。
        """
        return "".join(c for c in str(session_id) if c.isalnum() or c in "-_")

    def _path(self, session_id: str) -> Path:
        #仅允许由我们生成的 session id 形态，防路径穿越
        return self.data_dir / f"{self._safe_name(session_id)}.jsonl"

    def create(self) -> str:
        """创建会话并立即落盘空文件，确保新会话在列表中可见。"""
        with self._lock:
            sid = uuid.uuid4().hex[:24]
            self._path(sid).touch(exist_ok=True)
            self._status[sid] = self.STATUS_IDLE
            self._save_status()
            self._cache_valid = False
        return sid

    def exists(self, session_id: str) -> bool:
        with self._lock:
            if not self._safe_name(session_id):
                return False
            return self._path(session_id).exists()

    def append(self, session_id: str, message: dict[str, Any]) -> None:
        with self._lock:
            self._flush_pending(session_id)
            path = self._path(session_id)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(message, ensure_ascii=False) + "\n")
            self._cache_valid = False

    # 流式落盘节流：token 级 delta 先积压内存，按间隔批量写盘（避免每 token 全量读写文件）
    STREAM_FLUSH_INTERVAL = 0.4  # 秒

    def append_stream(self, session_id: str, role: str, text: str) -> None:
        """追加流式增量：合并进末尾的同 role 记录，避免 JSONL 行数爆炸。

        首 token 立即落盘（崩溃不丢起点），后续按 STREAM_FLUSH_INTERVAL 节流；
        节流期间数据暂存 _pending_delta，load/collapse/save 时强制合并。
        """
        if not text:
            return
        with self._lock:
            pending = self._pending_delta.get(session_id)
            if pending is None or pending["role"] != role:
                self._flush_pending(session_id)
                # 首个增量立即写入文件
                path = self._path(session_id)
                messages = self._load_unlocked(session_id)
                if messages and messages[-1].get("role") == role:
                    messages[-1]["content"] = (messages[-1].get("content") or "") + text
                    self.save(session_id, messages)
                else:
                    self.append(session_id, {"role": role, "content": text})
                self._pending_delta[session_id] = {
                    "role": role, "text": "", "last_flush": time.monotonic(),
                }
                self._cache_valid = False
                return
            pending["text"] += text
            now = time.monotonic()
            if now - pending["last_flush"] >= self.STREAM_FLUSH_INTERVAL:
                self._flush_pending(session_id)

    def _flush_pending(self, session_id: str) -> None:
        """将积压的流式增量合并进文件（须持有锁；文件已被写过的部分在 last_flush 前已入盘）。"""
        pending = self._pending_delta.pop(session_id, None)
        if pending is None or not pending["text"]:
            return
        path = self._path(session_id)
        messages = self._load_unlocked(session_id)
        role = pending["role"]
        if messages and messages[-1].get("role") == role:
            messages[-1]["content"] = (messages[-1].get("content") or "") + pending["text"]
            self._write_unlocked(session_id, messages)
        else:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"role": role, "content": pending["text"]}, ensure_ascii=False) + "\n")
        self._cache_valid = False

    def collapse_deltas(self, session_id: str) -> None:
        """一轮生成结束后，将连续的 assistant_delta 合并为单条 assistant 消息。"""
        with self._lock:
            self._flush_pending(session_id)
            messages = self._load_unlocked(session_id)
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
            self._cache_valid = False

    def _load_unlocked(self, session_id: str) -> list[dict[str, Any]]:
        """读取会话文件（须持有锁；不触发 pending 合并，纯文件内容）。"""
        if not self._safe_name(session_id):
            return []
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

    def _write_unlocked(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """覆写会话文件（须持有锁）。"""
        path = self._path(session_id)
        tmp = path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            for msg in messages:
                fh.write(json.dumps(msg, ensure_ascii=False) + "\n")
        tmp.replace(path)
        self._cache_valid = False

    def load(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            self._flush_pending(session_id)
            return self._load_unlocked(session_id)

    def clear(self, session_id: str) -> None:
        """清空会话内容但保留会话本身（文件截断为空）。"""
        with self._lock:
            self._pending_delta.pop(session_id, None)
            path = self._path(session_id)
            if path.exists():
                path.write_text("", encoding="utf-8")
            self._cache_valid = False

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
            self._cache_valid = False
            return existed

    def save(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        with self._lock:
            # 调用方传入的列表通常来自 load()（已含 pending 增量），直接丢弃缓冲
            self._pending_delta.pop(session_id, None)
            self._write_unlocked(session_id, messages)

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
            self._cache_valid = False
            return dest

    # 单个会话文件的元信息签名：(mtime_ns, size)
    _Sig = tuple[int, int]

    def _scan_file(self, path: Path, session_id: str) -> dict[str, Any] | None:
        """读取单个会话文件，生成列表条目（不含 mtime）；失败返回 None。"""
        # 一次遍历同时提取首条user 消息标题与可见消息计数
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
            stat = path.stat()
        except OSError:
            return None
        return {
            "id": session_id,
            "title": self._titles.get(session_id) or preview or "新会话",
            "message_count": count,
            "status": self.get_status(session_id),
            "created_at": dt.datetime.fromtimestamp(stat.st_ctime, tz=dt.timezone.utc).isoformat(),
            "updated_at": dt.datetime.fromtimestamp(stat.st_mtime, tz=dt.timezone.utc).isoformat(),
            "mtime": stat.st_mtime,
        }

    def _get_cached_session_list(self) -> list[dict[str, Any]]:
        """获取会话列表缓存（按文件签名增量刷新）。

        性能：每个文件以 (mtime_ns, size) 为签名，只有真正变更过的文件才重新
        解析内容。会话数上百时，全量重建需读取每个 JSONL 全文（单次数十 MB），
        增量方式把常态开销降到一次目录 stat。

        一致性：无论缓存是否标记失效，都以「目录实际内容」为准做一次对账，
        因此删除/归档/新建会话都能立即反映，不依赖调用方正确置失效标记。
        """
        with self._lock:
            # 与目录实际内容对账：删除/归档的会话在此被剔除
            live: dict[str, _Sig] = {}
            for path in self.data_dir.glob("*.jsonl"):
                try:
                    st = path.stat()
                except OSError:
                    continue
                live[path.stem] = (st.st_mtime_ns, st.st_size)

            # 标题表变化会影响展示（改名是低频操作，整体重建）
            titles_sig = tuple(sorted(self._titles.items()))
            titles_changed = titles_sig != self._titles_sig
            self._titles_sig = titles_sig

            # 剔除已不存在的会话
            for sid in set(self._session_cache) - set(live):
                self._session_cache.pop(sid, None)
                self._file_sigs.pop(sid, None)

            for session_id, sig in live.items():
                unchanged = (
                    not titles_changed
                    and self._file_sigs.get(session_id) == sig
                    and session_id in self._session_cache
                )
                if unchanged:
                    # 仅状态可能在内存中变化，刷新即可（无需重读文件）
                    self._session_cache[session_id]["status"] = self.get_status(session_id)
                    continue
                entry = self._scan_file(self._path(session_id), session_id)
                if entry is None:
                    continue
                self._session_cache[session_id] = entry
                self._file_sigs[session_id] = sig

            self._cache_valid = True
            return list(self._session_cache.values())

    def list_sessions(self) -> list[dict[str, Any]]:
        """返回会话列表，按最后更新时间倒序（最新在前）。

        兼容旧调用方：等价于 `list_sessions_page(limit=None)[0]`。
        """
        return self.list_sessions_page()[0]

    def list_sessions_page(
        self, *, limit: int | None = None, offset: int = 0, keyword: str = ""
    ) -> tuple[list[dict[str, Any]], int]:
        """分页返回会话列表与过滤后的总数。

        limit/offset 在排序后应用，避免全量构造；keyword 命中标题（忽略大小写）。
        """
        with self._lock:
            sessions = self._get_cached_session_list()
            if keyword:
                kw = keyword.lower()
                sessions = [s for s in sessions if kw in str(s.get("title", "")).lower()]
            sessions.sort(key=lambda s: s["mtime"], reverse=True)
            total = len(sessions)
            if limit is not None:
                sessions = sessions[offset: offset + limit]
            elif offset:
                sessions = sessions[offset:]
            # 复制并移除 mtime，避免污染缓存
            return [
                {k: v for k, v in s.items() if k != "mtime"} for s in sessions
            ], total
