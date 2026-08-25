"""OpenAI 兼容协议客户端与端点能力探测。

封装 /v1/models 与 /v1/chat/completions，支持流式与非流式调用；
网关启动时对每个端点做一次能力探测并缓存 modality，配置变化后热刷新。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger("aiassistant.llm")

DEFAULT_TIMEOUT = 120.0


@dataclass
class EndpointInfo:
    name: str
    base_url: str
    api_key: str
    model_name: str
    modality: str = "auto"      # auto|multimodal|text
    enabled: bool = True
    detected_modality: str | None = None  # 能力探测结果
    available: bool = False


def _build_client(base_url: str, api_key: str) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers=headers,
        timeout=DEFAULT_TIMEOUT,
    )


class LLMClient:
    """一个 OpenAI 兼容端点的客户端。"""

    def __init__(self, endpoint: EndpointInfo) -> None:
        self.info = endpoint
        self._client = _build_client(endpoint.base_url, endpoint.api_key)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def probe(self) -> None:
        """调用 /v1/models 探测端点可用性与模态能力。"""
        try:
            resp = await self._client.get("/models")
            resp.raise_for_status()
            data = resp.json()
            models = data.get("data", [])
            self.info.available = True
            self.info.detected_modality = self._infer_modality(models)
        except Exception as exc:  # noqa: BLE001
            self.info.available = False
            self.info.detected_modality = None
            logger.warning("端点 %s 探测失败: %s", self.info.name, exc)

    @staticmethod
    def _infer_modality(models: list[dict[str, Any]]) -> str:
        for m in models:
            caps = m.get("capabilities") or {}
            if caps.get("vision") or "vl" in m.get("id", "").lower() or "vision" in m.get("id", "").lower():
                return "multimodal"
        return "text"

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """非流式补全，返回 OpenAI 格式响应。"""
        payload: dict[str, Any] = {
            "model": self.info.model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        if max_tokens:
            payload["max_tokens"] = max_tokens
        resp = await self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def chat_completion_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式补全，逐块 yield SSE 解析后的 JSON 字典。"""
        payload: dict[str, Any] = {
            "model": self.info.model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        if max_tokens:
            payload["max_tokens"] = max_tokens
        async with self._client.stream(
            "POST", "/chat/completions", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue


class ModelRegistry:
    """管理主/视觉/压缩三类端点，支持探测与热刷新。"""

    def __init__(self, settings) -> None:
        self._settings = settings
        self._clients: dict[str, LLMClient] = {}
        self.reload()

    def reload(self) -> None:
        """按当前配置重建端点客户端并触发探测（异步探测见 probe_all）。"""
        for client in self._clients.values():
            # 不主动关闭复用，避免并发中断；替换引用即可，由 GC 回收
            pass
        new_clients: dict[str, LLMClient] = {}
        for name, ep in self._settings.endpoints().items():
            enabled = ep.get("enabled", True)
            if name == "vision" and not enabled:
                continue
            if name == "compressor" and not enabled:
                continue
            info = EndpointInfo(
                name=name,
                base_url=ep.get("base_url", ""),
                api_key=ep.get("api_key", ""),
                model_name=ep.get("model_name", name),
                modality=ep.get("modality", "auto"),
                enabled=enabled,
            )
            new_clients[name] = LLMClient(info)
        self._clients = new_clients

    async def probe_all(self) -> None:
        import asyncio

        async def _probe(name: str, client: LLMClient) -> None:
            await client.probe()

        await asyncio.gather(*(_probe(n, c) for n, c in self._clients.items()))

    def get(self, name: str) -> LLMClient | None:
        return self._clients.get(name)

    def snapshot(self) -> dict[str, Any]:
        """返回前端设置页所需的状态快照。"""
        out: dict[str, Any] = {}
        for name, client in self._clients.items():
            ep = client.info
            out[name] = {
                "name": name,
                "base_url": ep.base_url,
                "model_name": ep.model_name,
                "modality": ep.modality,
                "detected_modality": ep.detected_modality,
                "available": ep.available,
                "enabled": ep.enabled,
            }
        return out
