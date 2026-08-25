"""FastAPI 网关全局依赖（单例注册与生命周期）。

提供进程级共享单例（settings / registry / vision / compressor / skills_mgr /
mcp_mgr / executor / sessions / pipeline），以及应用启动 / 关闭钩子。
"""
from __future__ import annotations

import logging

from ..core import auth
from ..core.config import settings
from ..services.chat import ChatPipeline
from ..services.compress import ContextCompressor
from ..services.llm import ModelRegistry
from ..services.mcp_client import MCPManager
from ..services.sandbox import Executor
from ..services.sessions import SessionStore
from ..services.skills import SkillsManager
from ..services.vision import VisionRouter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("aiassistant.gateway")

# -- 全局单例 --
registry = ModelRegistry(settings)
vision = VisionRouter(settings, registry)
compressor = ContextCompressor(settings, registry)
skills_mgr = SkillsManager(executor=None)
mcp_mgr = MCPManager(settings)
executor = Executor(settings)
sessions = SessionStore()
pipeline = ChatPipeline(registry, vision, compressor, skills_mgr, mcp_mgr)

# skills 的执行器与 sandbox 保持一致
skills_mgr.executor = executor


async def startup() -> None:
    await registry.probe_all()
    mcp_status = await mcp_mgr.connect_all()
    logger.info("MCP 连接: %s", ", ".join(mcp_status))
    logger.info("端点探测: %s", registry.snapshot())


async def shutdown() -> None:
    await mcp_mgr.close_all()