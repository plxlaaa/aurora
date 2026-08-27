"""对话编排核心（文档 10.1）。

流程：
1. 视觉路由（如有图片）→ 选择主/视觉端点
2. 判断是否需要上下文压缩
3. 组装 tools（Skills + MCP），调用主模型流式推理
4. 处理 tool_call → 执行 skill/mcp → 结果回填模型，循环至无工具调用
5. 流式 delta 回推（通过 sink 回调）
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from .compress import ContextCompressor
from .llm import ModelRegistry
from .mcp_client import MCPManager
from .skills import SkillsManager
from .vision import VisionRouter

logger = logging.getLogger("aiassistant.chat")

DeltaSink = Callable[[dict[str, Any]], Awaitable[None]]

MAX_TOOL_ROUNDS = 5

# OpenAI 兼容接口允许的 role；网关记录用的 thinking/tool_call/assistant_delta 等不在此列
_LLM_ROLES = {"system", "user", "assistant", "tool"}


def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将网关会话记录转换为 LLM 可接受的标准消息列表。

    丢弃非标准 role（thinking/tool_call/assistant_delta 等展示性记录），
    保留 assistant 的 tool_calls 与 tool 的 tool_call_id 用于工具循环衔接。
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        if role not in _LLM_ROLES:
            continue
        content = msg.get("content")
        if content is None:
            content = ""
        normalized: dict[str, Any] = {"role": role, "content": content}
        if role == "assistant" and msg.get("tool_calls"):
            normalized["tool_calls"] = msg["tool_calls"]
        if role == "tool":
            if msg.get("tool_call_id"):
                normalized["tool_call_id"] = msg["tool_call_id"]
            if msg.get("name"):
                normalized["name"] = msg["name"]
        if role == "user" and msg.get("attachments"):
            normalized["attachments"] = msg["attachments"]
        out.append(normalized)
    return out


class ChatPipeline:
    def __init__(
        self,
        registry: ModelRegistry,
        vision: VisionRouter,
        compressor: ContextCompressor,
        skills: SkillsManager,
        mcp: MCPManager,
    ) -> None:
        self.registry = registry
        self.vision = vision
        self.compressor = compressor
        self.skills = skills
        self.mcp = mcp

    def collect_tools(self) -> list[dict[str, Any]]:
        return self.skills.function_definitions() + self.mcp.function_definitions()

    async def run_full(
        self,
        messages: list[dict[str, Any]],
        attachments: list[dict[str, Any]],
        sink: DeltaSink,
        *,
        force_compress: bool = False,
        temperature: float = 0.7,
        model_name: str | None = None,
        cancel: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        client = self.registry.get("main")
        if client is None:
            raise RuntimeError("主模型端点未配置")

        def cancelled() -> bool:
            return cancel is not None and cancel.is_set()

        # 网关记录格式 → OpenAI 标准消息（过滤 thinking/tool_call 等展示性记录）
        messages = _normalize_messages(messages)

        # 1. 视觉路由（如含图片）
        image_attachments = [a for a in attachments if a.get("type") == "image"]
        if image_attachments:
            endpoint_name, _ = self.vision.select_model()
            if endpoint_name != "main":
                vis_client = self.registry.get("vision")
                if vis_client is None:
                    raise RuntimeError("视觉端点不可用")
                vision_messages = list(messages)
                self.vision.attach_image_to_messages(vision_messages, image_attachments)
                return await self._vision_roundtrip(vis_client, vision_messages, sink, cancel)
            self.vision.attach_image_to_messages(messages, image_attachments)

        # 2. 上下文压缩
        if force_compress or self.compressor.should_compress(messages):
            logger.info("触发上下文压缩")
            await sink({"type": "tool_call", "name": "compress", "status": "running", "arguments": {}})
            messages = await self.compressor.compress(messages)
            await sink({"type": "tool_call", "name": "compress", "status": "done", "arguments": {}, "result": "已完成摘要压缩"})

        # 3+4. 主模型推理 + 工具循环
        tools = self.collect_tools()
        history = list(messages)

        for _round in range(MAX_TOOL_ROUNDS):
            if cancelled():
                await sink({"type": "cancelled"})
                return {"done": True, "cancelled": True}

            full_content = ""
            pending_calls: dict[int, dict[str, Any]] = {}

            async def handle_chunk(chunk: dict[str, Any]) -> None:
                nonlocal full_content
                choices = chunk.get("choices") or []
                if not choices:
                    return
                choice = choices[0]
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    full_content += delta["content"]
                    await sink({"type": "delta", "text": delta["content"]})
                # 兼容部分模型返回的 reasoning_content（思考过程）
                if delta.get("reasoning_content"):
                    await sink({"type": "thinking", "text": delta["reasoning_content"]})
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = pending_calls.setdefault(
                        idx,
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] += fn["arguments"]

            interrupted = False
            stream = client.chat_completion_stream(
                history,
                tools=tools or None,
                temperature=temperature,
                model_name=model_name,
            )
            try:
                async for chunk in stream:
                    if cancelled():
                        interrupted = True
                        break
                    await handle_chunk(chunk)
            finally:
                await stream.aclose()

            if interrupted:
                await sink({"type": "cancelled"})
                return {"done": True, "cancelled": True, "content": full_content}

            # 按 index 顺序还原工具调用，保持与模型输出一致
            completed_calls = [
                {
                    "id": pending_calls[idx]["id"],
                    "type": "function",
                    "function": {
                        "name": pending_calls[idx]["function"]["name"],
                        "arguments": pending_calls[idx]["function"]["arguments"],
                    },
                }
                for idx in sorted(pending_calls)
                if pending_calls[idx]["function"]["name"]
            ]

            assistant_msg: dict[str, Any] = {"role": "assistant", "content": full_content}
            if completed_calls:
                assistant_msg["tool_calls"] = completed_calls
            history.append(assistant_msg)

            if not completed_calls:
                await sink({"type": "done"})
                return {"done": True, "content": full_content}

            await self._execute_tools(completed_calls, history, sink, cancel)
            if cancelled():
                await sink({"type": "cancelled"})
                return {"done": True, "cancelled": True}

        await sink({"type": "done", "result": "达到最大工具调用轮次"})
        return {"done": True}

    async def _execute_tools(
        self,
        completed_calls: list[dict[str, Any]],
        history: list[dict[str, Any]],
        sink: DeltaSink,
        cancel: asyncio.Event | None = None,
    ) -> None:
        """执行模型请求的工具调用，将结果以 tool 消息回填历史。"""
        for call in completed_calls:
            name = call["function"]["name"]
            call_id = call.get("id") or name
            if cancel is not None and cancel.is_set():
                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": json.dumps({"error": "已取消"}, ensure_ascii=False),
                    }
                )
                continue
            try:
                arguments = json.loads(call["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {"input": arguments}
            await sink({
                "type": "tool_call",
                "id": call_id,
                "name": name,
                "status": "running",
                "arguments": arguments,
            })
            status = "done"
            try:
                if name.startswith("mcp_"):
                    result = await self.mcp.dispatch(name, arguments)
                else:
                    result = await self.skills.execute(name, arguments)
                if isinstance(result, dict) and result.get("error"):
                    status = "error"
            except Exception as exc:  # noqa: BLE001
                logger.warning("工具 %s 调用失败: %s", name, exc)
                result = {"error": f"工具调用失败: {exc}"}
                status = "error"
            await sink(
                {
                    "type": "tool_call",
                    "id": call_id,
                    "name": name,
                    "status": status,
                    "arguments": arguments,
                    "result": result,
                }
            )
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    # -- 视觉独立端点往返 --
    async def _vision_roundtrip(
        self, vis_client, messages, sink: DeltaSink, cancel: asyncio.Event | None = None
    ) -> dict[str, Any]:
        full = ""
        interrupted = False
        stream = vis_client.chat_completion_stream(messages, max_tokens=2048)
        try:
            async for chunk in stream:
                if cancel is not None and cancel.is_set():
                    interrupted = True
                    break
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                if delta.get("content"):
                    full += delta["content"]
                    await sink({"type": "delta", "text": delta["content"]})
                if delta.get("reasoning_content"):
                    await sink({"type": "thinking", "text": delta["reasoning_content"]})
        finally:
            await stream.aclose()
        if interrupted:
            await sink({"type": "cancelled"})
            return {"done": True, "cancelled": True, "content": full}
        await sink({"type": "tool_call", "name": "vision", "status": "done", "result": "已完成图片理解"})
        await sink({"type": "done"})
        return {"done": True, "content": full}
