"""用量统计插件：订阅生命周期事件，聚合调用计数与耗时。

纯内存统计（进程重启清零），通过 GET /api/stats 暴露快照。
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any

from ...core.hooks import HookBus

_MAX_SESSION_KEYS = 200  # 会话维度统计上限，防内存无界


class StatsPlugin:
    def __init__(self) -> None:
        self.started_at = time.time()
        self.counters = {
            "generate_started": 0,
            "generate_completed": 0,
            "generate_cancelled": 0,
            "generate_errors": 0,
            "tool_calls": 0,
            "tool_errors": 0,
            "compressions": 0,
        }
        self.total_generate_ms = 0
        self.total_tool_ms = 0
        self._per_session: "OrderedDict[str, int]" = OrderedDict()

    def _bump_session(self, session_id: str | None) -> None:
        if not session_id:
            return
        self._per_session[session_id] = self._per_session.get(session_id, 0) + 1
        self._per_session.move_to_end(session_id)
        while len(self._per_session) > _MAX_SESSION_KEYS:
            self._per_session.popitem(last=False)

    async def _on_generate_start(self, p: dict[str, Any]) -> None:
        self.counters["generate_started"] += 1
        self._bump_session(p.get("session_id"))

    async def _on_generate_end(self, p: dict[str, Any]) -> None:
        if p.get("cancelled"):
            self.counters["generate_cancelled"] += 1
        else:
            self.counters["generate_completed"] += 1
        self.total_generate_ms += int(p.get("duration_ms") or 0)
        self._bump_session(p.get("session_id"))

    async def _on_generate_error(self, p: dict[str, Any]) -> None:
        self.counters["generate_errors"] += 1
        self._bump_session(p.get("session_id"))

    async def _on_tool_after(self, p: dict[str, Any]) -> None:
        self.counters["tool_calls"] += 1
        if not p.get("ok"):
            self.counters["tool_errors"] += 1
        self.total_tool_ms += int(p.get("duration_ms") or 0)

    async def _on_compress(self, p: dict[str, Any]) -> None:
        self.counters["compressions"] += 1

    def register(self, hooks: HookBus) -> None:
        hooks.subscribe("generate.start", self._on_generate_start, name="stats", priority=50)
        hooks.subscribe("generate.end", self._on_generate_end, name="stats", priority=50)
        hooks.subscribe("generate.error", self._on_generate_error, name="stats", priority=50)
        hooks.subscribe("tool.after", self._on_tool_after, name="stats", priority=50)
        hooks.subscribe("compress.done", self._on_compress, name="stats", priority=50)

    def snapshot(self) -> dict[str, Any]:
        finished = self.counters["generate_completed"] + self.counters["generate_cancelled"]
        return {
            "uptime_s": int(time.time() - self.started_at),
            "counters": dict(self.counters),
            "avg_generate_ms": self.total_generate_ms // max(1, finished),
            "avg_tool_ms": self.total_tool_ms // max(1, self.counters["tool_calls"]),
            "active_sessions_tracked": len(self._per_session),
        }
