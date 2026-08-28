"""MCP（Model Context Protocol）客户端集成（文档 7）。

- 对 stdio server 以 asyncio 子进程拉起；SSE/HTTP 走对应客户端。
- 连接后拉取 tools/list，转换为网关 function definition，注入主模型。
- 模型发起 tool_call → 网关转发至对应 MCP server → 结果回填模型。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

logger = logging.getLogger("aiassistant.mcp")


class MCPServerHandle:
    def __init__(self, name: str, cfg: dict[str, Any]) -> None:
        self.name = name
        self.cfg = cfg
        self.session: ClientSession | None = None
        self.tools: list[dict[str, Any]] = []
        self.available = False

    async def connect(self, http_timeout: float = 15.0) -> None:
        transport = self.cfg.get("transport", "stdio")
        if transport == "stdio":
            await self._connect_stdio()
        elif transport in ("http", "sse"):
            await self._connect_http(http_timeout)
        else:
            raise ValueError(f"不支持的 MCP transport: {transport}")

    async def _connect_stdio(self) -> None:
        params = StdioServerParameters(
            command=self.cfg["command"],
            args=self.cfg.get("args", []),
            env=self.cfg.get("env"),
            cwd=self.cfg.get("cwd"),
        )
        self._stdio_ctx = stdio_client(params)
        self._read, self._write = await self._stdio_ctx.__aenter__()
        self.session = await ClientSession(self._read, self._write).__aenter__()
        await self.session.initialize()
        await self._load_tools()

    async def _connect_http(self, timeout: float) -> None:
        url = self.cfg["url"]
        headers = {}
        api_key = self.cfg.get("api_key")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http_ctx = streamable_http_client(url, headers=headers, timeout=timeout)
        self._read, self._write = await self._http_ctx.__aenter__()
        self.session = await ClientSession(self._read, self._write).__aenter__()
        await self.session.initialize()
        await self._load_tools()

    async def _load_tools(self) -> None:
        if self.session is None:
            return
        tools_result = await self.session.list_tools()
        self.tools = tools_result.tools
        self.available = True

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.session is None:
            raise RuntimeError(f"MCP server {self.name} 未连接")
        try:
            result = await self.session.call_tool(name, arguments)
            text = ""
            for content in result.content:
                if getattr(content, "type", None) == "text":
                    text += getattr(content, "text", "") or ""
            return {"result": text or "（无文本输出）"}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"MCP 调用失败: {exc}"}

    async def close(self) -> None:
        for ctx in (getattr(self, "_stdio_ctx", None), getattr(self, "_http_ctx", None)):
            if ctx is not None:
                try:
                    await ctx.__aexit__(None, None, None)
                except Exception:  # noqa: BLE001
                    pass


class MCPManager:
    def __init__(self, settings) -> None:
        self._settings = settings
        self._handles: dict[str, MCPServerHandle] = {}
        self._pending_close: list[MCPServerHandle] = []
        self.reload()

    def reload(self) -> None:
        new_handles: dict[str, MCPServerHandle] = {}
        for cfg in self._settings.mcp_servers():
            if not cfg.get("enabled", True):
                continue
            name = cfg.get("name")
            if not name:
                continue
            new_handles[name] = MCPServerHandle(name, cfg)
        self._handles = new_handles

    def _http_timeout(self) -> float:
        return float(self._settings.raw("mcp").get("http", {}).get("timeout", 15))

    async def connect_one(self, name: str) -> str:
        """连接（或重连）单个 server；返回状态描述字符串。"""
        handle = self._handles.get(name)
        if handle is None:
            return f"{name}(未配置或未启用)"
        await handle.close()
        try:
            await handle.connect(http_timeout=self._http_timeout())
            return f"{name}({len(handle.tools)} tools)"
        except Exception as exc:  # noqa: BLE001
            logger.warning("MCP server %s 连接失败: %s", name, exc)
            return f"{name}(failed: {exc})"

    def upsert(self, cfg: dict[str, Any]) -> None:
        """新增或更新单个 server 配置（仅内存句柄，持久化由 Settings 负责）。"""
        name = cfg.get("name")
        if not name:
            raise ValueError("MCP server 配置缺少 name")
        old = self._handles.get(name)
        if old is not None:
            self._pending_close.append(old)
        handle = MCPServerHandle(name, cfg)
        if not cfg.get("enabled", True):
            # 禁用状态：句柄不入注册表
            if old is None:
                return
            del self._handles[name]
            return
        self._handles[name] = handle

    def remove(self, name: str) -> MCPServerHandle | None:
        """移除 server 句柄并返回（由调用方决定是否关闭连接）。"""
        return self._handles.pop(name, None)

    def get_handle(self, name: str) -> MCPServerHandle | None:
        return self._handles.get(name)

    async def close_handle(self, handle: MCPServerHandle) -> None:
        await handle.close()

    async def connect_all(self) -> list[str]:
        """连接所有启用的 server，返回成功连接的工具计数摘要。"""
        results: list[str] = []
        for name, handle in self._handles.items():
            try:
                await handle.connect(http_timeout=self._http_timeout())
                results.append(f"{name}({len(handle.tools)} tools)")
            except Exception as exc:  # noqa: BLE001
                logger.warning("MCP server %s 连接失败: %s", name, exc)
                results.append(f"{name}(failed: {exc})")
        return results

    async def close_all(self) -> None:
        for handle in self._handles.values():
            await handle.close()
        pending, self._pending_close = self._pending_close, []
        for handle in pending:
            await handle.close()

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "available": handle.available,
                "tools": [t.name for t in handle.tools],
                "tool_count": len(handle.tools),
            }
            for name, handle in self._handles.items()
        ]

    def function_definitions(self) -> list[dict[str, Any]]:
        defs: list[dict[str, Any]] = []
        for handle in self._handles.values():
            if not handle.available:
                continue
            for tool in handle.tools:
                defs.append(
                    {
                        "type": "function",
                        "function": {
                            "name": f"mcp_{handle.name}_{tool.name}",
                            "description": tool.description or f"MCP 工具 {handle.name}/{tool.name}",
                            "parameters": getattr(tool, "inputSchema", None)
                            or getattr(tool, "input_schema", None)
                            or {"type": "object", "properties": {}},
                        },
                    }
                )
        return defs

    async def dispatch(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """按 'mcp_<server>_<tool>' 命名解析并调用。"""
        if not tool_name.startswith("mcp_"):
            raise KeyError(f"非 MCP 工具: {tool_name}")
        parts = tool_name.split("_", 2)
        if len(parts) != 3:
            raise KeyError(f"非法 MCP 工具名: {tool_name}")
        _, server_name, tool = parts
        handle = self._handles.get(server_name)
        if handle is None:
            raise KeyError(f"MCP server 不存在: {server_name}")
        return await handle.call_tool(tool, arguments)
