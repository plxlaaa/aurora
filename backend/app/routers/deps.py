"""FastAPI 网关全局依赖（单例注册与生命周期）。

提供进程级共享单例（settings / registry / vision / compressor / skills_mgr /
mcp_mgr / executor / custom_tools / tool_registry / sessions / pipeline），
以及应用启动 / 关闭钩子。

生命周期约定：
- `startup()`：加载内置插件 → 并发探测模型端点 → 连接全部 MCP server；
- `shutdown()`：关闭 MCP 连接与所有模型端点的 HTTP 连接池（顺序相反，先外后内），
  避免 uvicorn 重载/退出时泄漏连接。
"""
from __future__ import annotations

import logging

from ..core import auth
from ..core.config import settings
from ..core.hooks import HookBus
from ..core.runtime import runtime
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
    level=getattr(logging, runtime.log_level, logging.INFO),
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
    logger.info("MCP 连接: %s", ", ".join(mcp_status) if mcp_status else "(无)")
    logger.info("端点探测: %s", registry.snapshot())
    logger.info("钩子订阅: %s", hooks.snapshot())
    if not registry.any_available():
        logger.warning(
            "当前没有可用模型端点；/api/health 仍返回 ok，但对话请求会失败。"
            "请在设置页检查 base_url / api_key，或确认模型服务已启动。"
        )


async def shutdown() -> None:
    """按启动相反顺序释放资源；单步失败不阻断其余清理。"""
    for name, closer in (("MCP", mcp_mgr.close_all), ("模型端点", registry.close_all)):
        try:
            await closer()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s 连接关闭异常（已忽略）: %s", name, exc)


def readiness() -> dict:
    """就绪探针数据：模型端点与 MCP 连接概况（供 /api/health 与运维监控使用）。"""
    endpoints = registry.snapshot()
    available = [name for name, ep in endpoints.items() if ep.get("available")]
    return {
        "endpoints_total": len(endpoints),
        "endpoints_available": available,
        "ready": bool(available) or not endpoints,
        "mcp_servers": mcp_mgr.list(),
        "auth_enabled": auth.auth_enabled(),
        "sandbox_mode": settings.sandbox_mode(),
        "plugins": sorted(plugins),
        "version": "1.1.0",
    }