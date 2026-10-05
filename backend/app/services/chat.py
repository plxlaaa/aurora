"""对话编排核心（文档 10.1）。

流程：
1. 视觉路由（如有图片）→ 选择主/视觉端点
2. 判断是否需要上下文压缩
3. 注入工具目录 system prompt（含文本协议说明），调用主模型流式推理
4. 处理工具调用（原生 tool_calls 或 <tool_call> 文本标记）→ ToolRegistry 分发执行
   → 结果回填模型，循环至无工具调用
5. 流式 delta 回推（通过 sink 回调）
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Awaitable, Callable

from .compress import ContextCompressor
from .llm import ModelRegistry
from .tool_registry import ToolRegistry
from .vision import VisionRouter

logger = logging.getLogger("aiassistant.chat")

DeltaSink = Callable[[dict[str, Any]], Awaitable[None]]

MAX_TOOL_ROUNDS = 5

# 文本协议（ReAct）调用标记：<tool_call>{"name": ..., "arguments": {...}}</tool_call>
# 容忍 toolcall/toolCall 等标签变体与大小写；内容允许夹杂 markdown 代码块围栏
TOOL_CALL_RE = re.compile(
    r"<\s*tool_?call\s*>\s*(.*?)\s*<\s*/\s*tool_?call\s*>", re.DOTALL | re.IGNORECASE
)

# 模型输出 JSON 时的常见键名变体（name / arguments 及其同义词）
_CALL_NAME_KEYS = ("name", "tool", "tool_name", "toolCall")
_CALL_ARGS_KEYS = ("arguments", "args", "parameters", "input", "params")

_MD_FENCE_RE = re.compile(
    r"^\s*`{3,}[a-zA-Z0-9_-]*\s*\n?(?P<body>.*?)\n?\s*`{3,}\s*$", re.DOTALL
)

# OpenAI 兼容接口允许的 role；网关记录用的 thinking/tool_call/assistant_delta 等不在此列
_LLM_ROLES = {"system", "user", "assistant", "tool"}


def _strip_md_fence(text: str) -> str:
    """剥离模型在 JSON 外层包裹的 markdown 代码块围栏。"""
    stripped = (text or "").strip()
    match = _MD_FENCE_RE.match(stripped)
    if match and "{" in match.group("body"):
        return match.group("body").strip()
    return stripped


def _payload_to_call(payload: Any) -> dict[str, Any] | None:
    """把模型输出的调用 JSON 归一化为 {name, arguments}；形态不符返回 None。

    兼容：name/arguments 正常形态、tool/tool_name 等键名变体、
    arguments 为 JSON 字符串的双重编码、OpenAI tool_calls 的 function 嵌套形态。
    """
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("function"), dict):
        payload = payload["function"]
    name = next(
        (payload[k] for k in _CALL_NAME_KEYS if isinstance(payload.get(k), str) and payload[k]),
        None,
    )
    if not name:
        return None
    args: Any = next((payload[k] for k in _CALL_ARGS_KEYS if k in payload), None)
    if isinstance(args, str):
        # 双重编码：arguments 实为 JSON 字符串，尝试还原为对象
        try:
            decoded = json.loads(args)
        except json.JSONDecodeError:
            decoded = None
        args = decoded if isinstance(decoded, dict) else ({"input": args} if args else {})
    elif not isinstance(args, dict):
        args = {"input": args} if args is not None else {}
    return {"name": name, "arguments": args}


def _format_call(call_id: str, normalized: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": normalized["name"],
            "arguments": json.dumps(normalized["arguments"], ensure_ascii=False),
        },
    }


def parse_react_calls(text: str) -> list[dict[str, Any]]:
    """解析文本协议中的 <tool_call> 标记，返回与原生 tool_calls 同构的调用列表。"""
    calls: list[dict[str, Any]] = []
    for i, match in enumerate(TOOL_CALL_RE.finditer(text or "")):
        raw = _strip_md_fence(match.group(1))
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("文本协议调用标记 JSON 非法，已跳过")
            continue
        normalized = _payload_to_call(payload)
        if normalized is None:
            continue
        calls.append(_format_call(f"react_{i + 1}", normalized))
    return calls


def parse_bare_json_calls(text: str) -> list[dict[str, Any]]:
    """裸 JSON 兜底：模型省略标记、直接输出完整调用 JSON 时解析为候选调用。

    仅当剥离代码围栏后的整段文本恰好是一个 JSON 对象时才解析——
    不做全文扫描，避免模型在正文里举例说明工具用法（如「格式为 {"name": ...}」）
    时被误判为调用指令。是否为已注册工具由调用方校验。
    """
    stripped = _strip_md_fence((text or "").strip()).strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return []
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return []
    normalized = _payload_to_call(payload)
    if normalized is None:
        return []
    return [_format_call("react_bare_1", normalized)]


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
        tools: ToolRegistry,
    ) -> None:
        self.registry = registry
        self.vision = vision
        self.compressor = compressor
        self.tools = tools

    def collect_tools(self) -> list[dict[str, Any]]:
        return self.tools.collect_tools()

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
                vision_messages = self._inject_system(
                    list(messages), self.tools.build_directory()
                )
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
        history = self._inject_system(list(messages), self.tools.build_directory())

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
                # 无原生调用：解析文本协议标记（ReAct 回退），再尝试裸 JSON 兜底
                react_calls = parse_react_calls(full_content)
                if not react_calls:
                    react_calls = [
                        call
                        for call in parse_bare_json_calls(full_content)
                        if self.tools.name_exists(call["function"]["name"])
                    ]
                if not react_calls:
                    await sink({"type": "done"})
                    return {"done": True, "content": full_content}
                await self._execute_tools(react_calls, history, sink, cancel, native=False)
            else:
                await self._execute_tools(completed_calls, history, sink, cancel, native=True)
            if cancelled():
                await sink({"type": "cancelled"})
                return {"done": True, "cancelled": True}

        await sink({"type": "done", "result": "达到最大工具调用轮次"})
        return {"done": True}

    @staticmethod
    def _inject_system(history: list[dict[str, Any]], directory_text: str) -> list[dict[str, Any]]:
        """将工具目录注入 system prompt；已有 system 时合并到头部。"""
        if history and history[0].get("role") == "system":
            history[0]["content"] = directory_text + "\n\n" + str(history[0].get("content", ""))
        else:
            history.insert(0, {"role": "system", "content": directory_text})
        return history

    async def _execute_tools(
        self,
        completed_calls: list[dict[str, Any]],
        history: list[dict[str, Any]],
        sink: DeltaSink,
        cancel: asyncio.Event | None = None,
        *,
        native: bool = True,
    ) -> None:
        """执行模型请求的工具调用并回填历史。

        native=True 时以 role=tool + tool_call_id 回填（原生协议）；
        native=False 时以 user 消息包裹 <tool_result> 回填（文本协议）。
        """
        for call in completed_calls:
            name = call["function"]["name"]
            call_id = call.get("id") or name
            if cancel is not None and cancel.is_set():
                history.append(
                    {
                        "role": "tool" if native else "user",
                        **(
                            {"tool_call_id": call_id, "name": name}
                            if native
                            else {}
                        ),
                        "content": json.dumps({"error": "已取消"}, ensure_ascii=False)
                        if native
                        else "工具调用已取消，请直接基于已有信息继续。",
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
                result = await self.tools.dispatch(name, arguments)
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
            result_text = json.dumps(result, ensure_ascii=False, default=str)
            if native:
                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": result_text,
                    }
                )
                # 通知网关持久化 role=tool 结果（前端据此更新工具卡片）
                await sink(
                    {
                        "type": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": result_text,
                    }
                )
            else:
                backfill = (
                    f'<tool_result name="{name}">{result_text}</tool_result>\n'
                    "请根据工具结果继续。"
                )
                history.append({"role": "user", "content": backfill})
                # 文本协议回填事件；字段避开 content 以免前端误渲染为正文
                await sink(
                    {
                        "type": "tool_result",
                        "name": name,
                        "result_text": result_text,
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
