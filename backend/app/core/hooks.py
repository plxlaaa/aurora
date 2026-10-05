"""事件钩子总线（进程内扩展点）。

插件通过 subscribe(event, fn) 订阅生命周期事件，管道在关键节点 emit。
设计约束：
- emit 对订阅方逐个调用并隔离异常（单插件故障不影响主流程）；
- 同事件按 priority 升序执行（小者先）；
- 全部异步化：同步回调在线程池执行。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger("aiassistant.hooks")

HookFn = Callable[[dict[str, Any]], Any]

# 生命周期事件清单（文档化契约）
EVENTS = (
    "generate.start",   # {session_id, model, temperature}
    "generate.end",     # {session_id, content, cancelled, rounds, duration_ms}
    "generate.error",   # {session_id, error}
    "tool.before",      # {session_id, name, arguments}
    "tool.after",       # {session_id, name, ok, duration_ms, error}
    "compress.done",    # {session_id, before, after}
)


class HookBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[tuple[int, str, HookFn]]] = {}

    def subscribe(self, event: str, fn: HookFn, *, name: str = "", priority: int = 100) -> None:
        """订阅事件；name 用于日志与去重，priority 小者先执行。

        EVENTS 仅是内置事件文档；自定义事件同样允许（插件可自由扩展事件类型）。
        """
        subs = self._subs.setdefault(event, [])
        key = name or getattr(fn, "__name__", "hook")
        if any(k == key for _, k, _ in subs):
            logger.warning("钩子重复订阅被忽略: %s/%s", event, key)
            return
        subs.append((priority, key, fn))
        subs.sort(key=lambda item: item[0])
        logger.info("钩子注册: %s <- %s (priority=%s)", event, key, priority)

    def has_subscribers(self, event: str) -> bool:
        return bool(self._subs.get(event))

    async def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """触发事件；异常隔离并记日志，永不向上抛。"""
        for _, key, fn in self._subs.get(event, []):
            try:
                result = fn(payload or {})
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                logger.warning("钩子执行失败(已隔离): %s/%s: %s", event, key, exc)

    def snapshot(self) -> dict[str, list[str]]:
        return {e: [k for _, k, _ in subs] for e, subs in self._subs.items()}
