"""FastAPI 网关全局依赖（单例注册与生命周期）。

提供进程级共享单例（settings / registry / vision / compressor / skills_mgr /
mcp_mgr / executor / custom_tools / tool_registry / sessions / pipeline），
以及应用启动 / 关闭钩子。
"""
from __future__ import annotations

import logging

from ..core import auth
from ..core.config import settings
from ..core.hooks import HookBus
from ..services.chat import ChatPipeline
from ..services.compress import ContextCompressor
from ..services.custom_tools import CustomToolsManager
from ..services.llm import ModelRegistry
from ..services.mcp_client import MCPManager
from ..services.plugins import load_builtin_plugins
from ..services.sandbox import Executor
from ..services.sessions import SessionStore
from ..services.skills import SkillsManager
from ..services.tool_registry import ToolRegistry
from ..services.vision import VisionRouter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("aiassistant.gateway")

# -- 全局单例 --
hooks = HookBus()
registry = ModelRegistry(settings)
vision = VisionRouter(settings, registry)
compressor = ContextCompressor(settings, registry)
skills_mgr = SkillsManager(executor=None)
mcp_mgr = MCPManager(settings)
executor = Executor(settings)
custom_tools = CustomToolsManager(settings.config_dir, executor=executor)
tool_registry = ToolRegistry(skills_mgr, mcp_mgr, custom_tools, executor=executor)
sessions = SessionStore()
pipeline = ChatPipeline(registry, vision, compressor, tool_registry, hooks=hooks)
# 内置插件（启动时填充）：{name: instance}
plugins: dict = {}


async def startup() -> None:
    global plugins
    plugins.update(load_builtin_plugins(hooks, sessions, registry))
    await registry.probe_all()
    mcp_status = await mcp_mgr.connect_all()
    logger.info("MCP 连接: %s", ", ".join(mcp_status))
    logger.info("端点探测: %s", registry.snapshot())
    logger.info("钩子订阅: %s", hooks.snapshot())


async def shutdown() -> None:
    await mcp_mgr.close_all()