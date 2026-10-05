"""内置插件（钩子消费者）。

load_builtin_plugins(hooks, ...) 在应用启动时注册所有内置插件。
后续新增插件：在本目录新建模块并在 load_builtin_plugins 中登记即可，
无需改动管道代码。
"""
from __future__ import annotations

import logging
from typing import Any

from ...core.hooks import HookBus
from ...services.sessions import SessionStore
from .auto_title import AutoTitlePlugin
from .stats import StatsPlugin

logger = logging.getLogger("aiassistant.plugins")

__all__ = ["StatsPlugin", "AutoTitlePlugin", "load_builtin_plugins"]


def load_builtin_plugins(
    hooks: HookBus,
    sessions: SessionStore,
    registry: Any,
) -> dict[str, Any]:
    """注册全部内置插件，返回 {name: instance} 供 API 层引用。"""
    stats = StatsPlugin()
    auto_title = AutoTitlePlugin(sessions, registry)
    stats.register(hooks)
    auto_title.register(hooks)
    logger.info("内置插件已加载: stats, auto_title")
    return {"stats": stats, "auto_title": auto_title}
