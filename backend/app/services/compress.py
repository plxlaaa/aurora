"""上下文压缩（文档 5.3）。

- 触发：会话 token 数超过 compress.threshold 或手动触发。
- map_reduce：分块 → 小模型逐块摘要 → 合并摘要；保留系统提示与最近 K 轮原文。
- 回注：以 [context_summary] 标签置于系统提示之后，原超长历史移除。
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("aiassistant.compress")

SUMMARY_TAG = "[context_summary]"
_SYSTEM_ROLE = "system"


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """粗略估算 token 数（中文按 ~1 token/字符，英文按 ~4 字符/token）。"""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    parts.append(part.get("text", ""))
                    if "image_url" in part:
                        parts.append(" [image] ")
                else:
                    parts.append(str(part))
            content = "".join(parts)
        content = str(content)
        cjk = len(re.findall(r"[\u4e00-\u9fff]", content))
        ascii_chars = len(content) - cjk
        total += cjk + ascii_chars // 4 + 8
    return max(total, 1)


def _split_into_chunks(messages: list[dict[str, Any]], chunk_chars: int = 2000):
    chunks: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    cur_len = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
        length = len(str(content))
        if cur_len + length > chunk_chars and cur:
            chunks.append(cur)
            cur, cur_len = [], 0
        cur.append(msg)
        cur_len += length
    if cur:
        chunks.append(cur)
    return chunks


class ContextCompressor:
    def __init__(self, settings, registry) -> None:
        self._settings = settings
        self._registry = registry

    def should_compress(self, messages: list[dict[str, Any]]) -> bool:
        cfg = self._settings.compress()
        if not cfg.get("enabled", True):
            return False
        threshold = cfg.get("threshold", 0.8)
        window = cfg.get("context_window", 8192)
        return estimate_tokens(messages) > window * threshold

    async def compress(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """执行压缩并返回回注后的新消息列表（不含原始超长历史）。"""
        cfg = self._settings.compress()
        keep_recent = cfg.get("keep_recent_turns", 6)

        system_parts: list[str] = []
        body: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == _SYSTEM_ROLE:
                system_parts.append(str(msg.get("content", "")))
            else:
                body.append(msg)

        # 保留最近 K 轮原文：边界必须落在真实 user 消息上（跳过工具结果包装），
        # 否则截断会把 tool_call/tool 配对切断，压缩后的消息序列非法（LLM 400）
        keep_count = keep_recent * 2

        def _is_real_user(msg: dict[str, Any]) -> bool:
            return msg.get("role") == "user" and not str(
                msg.get("content") or ""
            ).startswith("<tool_result")

        boundary = max(0, len(body) - keep_count)
        while boundary > 0 and not _is_real_user(body[boundary]):
            boundary -= 1
        recent = body[boundary:]
        to_summarize = body[:boundary]

        strategy = cfg.get("strategy", "map_reduce")
        if strategy == "truncate" or not to_summarize:
            summary = self._truncate_summary(to_summarize)
        else:
            summary = await self._map_reduce(to_summarize, cfg)

        system_text = "\n".join(system_parts)
        if summary:
            system_text = f"{system_text}\n\n{SUMMARY_TAG}\n{summary}".strip()

        new_messages: list[dict[str, Any]] = []
        if system_text:
            new_messages.append({"role": _SYSTEM_ROLE, "content": system_text})
        new_messages.extend(recent)
        return new_messages

    @staticmethod
    def _truncate_summary(messages: list[dict[str, Any]]) -> str:
        if not messages:
            return ""
        lines = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in content
                )
            lines.append(f"{msg.get('role')}: {content}")
        # 保留首尾，裁剪中间
        text = "\n".join(lines)
        if len(text) <= 1500:
            return text
        return text[:750] + "\n...[中间省略]...\n" + text[-750:]

    async def _map_reduce(
        self, messages: list[dict[str, Any]], cfg: dict[str, Any]
    ) -> str:
        client = self._registry.get("compressor") or self._registry.get("main")
        if client is None:
            logger.warning("压缩模型未启用且主模型不可用，回退 truncate")
            return self._truncate_summary(messages)

        prompt_template = cfg.get("prompt", "请将以下对话历史压缩为一份简报。")
        # 摘要只吃正文：thinking/tool_call 为展示性记录（tool_call 的 content 常为 None），
        # 混入会产生 "None" 噪音；tool 记录保留（含事实结果）
        summary_messages = [
            m for m in messages
            if m.get("role") not in ("thinking", "tool_call")
        ]
        chunks = _split_into_chunks(summary_messages)
        partials: list[str] = []
        for chunk in chunks:
            rendered = "\n".join(
                f"{m.get('role')}: {m.get('content')}" for m in chunk
            )
            try:
                resp = await client.chat_completion(
                    [
                        {"role": "system", "content": prompt_template},
                        {"role": "user", "content": rendered},
                    ],
                    max_tokens=512,
                )
                partials.append(self._extract_text(resp))
            except Exception as exc:  # noqa: BLE001
                logger.warning("分块压缩失败: %s", exc)
                partials.append(self._truncate_summary(chunk))

        if len(partials) <= 1:
            return partials[0] if partials else ""

        merged = "\n".join(f"- {p.strip()}" for p in partials)
        try:
            resp = await client.chat_completion(
                [
                    {"role": "system", "content": prompt_template},
                    {
                        "role": "user",
                        "content": f"请合并以下多段摘要，去除重复，保留关键事实：\n{merged}",
                    },
                ],
                max_tokens=768,
            )
            return self._extract_text(resp)
        except Exception as exc:  # noqa: BLE001
            logger.warning("合并摘要失败，返回分段摘要: %s", exc)
            return merged

    @staticmethod
    def _extract_text(resp: dict[str, Any]) -> str:
        try:
            return resp["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            return ""
