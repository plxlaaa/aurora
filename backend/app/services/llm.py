"""OpenAI 兼容协议客户端与端点能力探测。

封装 /v1/models 与 /v1/chat/completions，支持流式与非流式调用；
网关启动时对每个端点做一次能力探测并缓存 modality，配置变化后热刷新。
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger("aiassistant.llm")

# 瞬时错误重试：公益站常见限流/网关抖动（403 WAF / 429 / 5xx），退避后重试一次
_TRANSIENT_STATUS = {403, 408, 429, 500, 502, 503, 504}
_TRANSIENT_MAX = 2
_TRANSIENT_BACKOFF = 3.0

DEFAULT_TIMEOUT = 120.0
# 探测类请求应快速失败，避免慢端点拖住启动/保存配置的交互
PROBE_TIMEOUT = 15.0
# 单端点连接池上限：与网关并发生成上限匹配，避免连接泄漏与端口耗尽
POOL_MAX_CONNECTIONS = 16
POOL_MAX_KEEPALIVE = 8


@dataclass
class EndpointInfo:
    name: str
    base_url: str
    api_key: str
    model_name: str
    modality: str = "auto"      # auto|multimodal|text
    enabled: bool = True
    codex_compat: bool = False  # 反代模式：模型自带 codex 提示词，网关仅提供 codex 工具链
    detected_modality: str | None = None  # 能力探测结果
    available: bool = False
    models: list[str] = field(default_factory=list)  # 探测到的可用模型 id
    error: str = ""                                  # 最近一次探测失败原因


def _build_client(base_url: str, api_key: str, timeout: float = DEFAULT_TIMEOUT) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers=headers,
        timeout=timeout,
        limits=httpx.Limits(
            max_connections=POOL_MAX_CONNECTIONS,
            max_keepalive_connections=POOL_MAX_KEEPALIVE,
            keepalive_expiry=30.0,
        ),
    )


class LLMClient:
    """一个 OpenAI 兼容端点的客户端。"""

    def __init__(self, endpoint: EndpointInfo) -> None:
        self.info = endpoint
        self._client = _build_client(endpoint.base_url, endpoint.api_key)
        # 记录不支持 function calling 的模型名，避免反复失败重试
        self._tools_blocked: set[str] = set()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def probe(self) -> None:
        """调用 /v1/models 探测端点可用性、模态能力与可用模型列表。"""
        if not self.info.base_url:
            self.info.available = False
            self.info.detected_modality = None
            self.info.models = []
            self.info.error = "未配置 base_url"
            return
        try:
            # 探测用更短超时：慢/不可达端点不应拖住启动与配置保存的响应
            resp = await self._client.get("/models", timeout=PROBE_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            models = data.get("data", [])
            self.info.available = True
            self.info.detected_modality = self._infer_modality(models)
            self.info.models = [m.get("id", "") for m in models if m.get("id")]
            self.info.error = ""
        except Exception as exc:  # noqa: BLE001
            self.info.available = False
            self.info.detected_modality = None
            self.info.models = []
            self.info.error = str(exc)[:200]
            logger.warning("端点 %s 探测失败: %s", self.info.name, exc)

    @staticmethod
    def _infer_modality(models: list[dict[str, Any]]) -> str:
        for m in models:
            caps = m.get("capabilities") or {}
            if caps.get("vision") or "vl" in m.get("id", "").lower() or "vision" in m.get("id", "").lower():
                return "multimodal"
        return "text"

    @staticmethod
    def _is_tools_blocked_error(body: bytes) -> bool:
        """判断 400 是否因模型不支持 function calling 所致。

        各中转站报错文案不统一，宽松匹配「工具/函数调用」+「不支持/未启用」组合，
        避免漏判导致报错直抛而非去掉 tools 降级重试。
        """
        text = body.decode("utf-8", "ignore").lower()
        mentions_tools = any(
            kw in text
            for kw in ("function call", "tool use", "tool call", '"tools"', "函数调用", "工具调用", "工具")
        )
        rejected = any(
            kw in text
            for kw in ("not support", "unsupported", "not enabled", "disabled", "不支持", "未启用", "不支持工具")
        )
        return mentions_tools and rejected

    @staticmethod
    def _http_error(resp: httpx.Response, body: bytes) -> httpx.HTTPStatusError:
        """构造带响应体的 HTTPStatusError，便于排查 4xx/5xx 根因。"""
        snippet = body.decode("utf-8", "ignore").strip()[:500]
        message = f"{resp.status_code} {resp.reason_phrase}"
        if snippet:
            message = f"{message} - {snippet}"
        return httpx.HTTPStatusError(message, request=resp.request, response=resp)

    def _maybe_retry_without_tools(
        self, resp: httpx.Response, body: bytes, model: str | None = None
    ) -> bool:
        """工具调用被拒绝时记录模型并返回 True，调用方应去掉 tools 重试。"""
        if self._is_tools_blocked_error(body):
            self._tools_blocked.add(model or self.info.model_name)
            return True
        return False

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """非流式补全，返回 OpenAI 格式响应。"""
        model = model_name or self.info.model_name
        transient_retries = 0
        while True:
            use_tools = tools
            if use_tools and model in self._tools_blocked:
                use_tools = None
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "stream": False,
            }
            if use_tools:
                payload["tools"] = use_tools
            if max_tokens:
                payload["max_tokens"] = max_tokens
            resp = await self._client.post("/chat/completions", json=payload)
            if resp.status_code == 400 and use_tools:
                body = resp.content
                if self._maybe_retry_without_tools(resp, body, model):
                    continue
                raise self._http_error(resp, body)
            if resp.status_code in _TRANSIENT_STATUS and transient_retries < _TRANSIENT_MAX:
                transient_retries += 1
                logger.warning(
                    "端点瞬时错误 HTTP %s，%ss 后重试(%s/%s): %s",
                    resp.status_code, _TRANSIENT_BACKOFF, transient_retries, _TRANSIENT_MAX, model,
                )
                await asyncio.sleep(_TRANSIENT_BACKOFF)
                continue
            if resp.status_code >= 400:
                raise self._http_error(resp, resp.content)
            return resp.json()

    async def chat_completion_stream(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        model_name: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式补全，逐块 yield SSE 解析后的 JSON 字典。"""
        model = model_name or self.info.model_name
        stream_retries = 0
        while True:
            use_tools = tools
            if use_tools and model in self._tools_blocked:
                use_tools = None
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "stream": True,
            }
            if use_tools:
                payload["tools"] = use_tools
            if max_tokens:
                payload["max_tokens"] = max_tokens
            async with self._client.stream(
                "POST", "/chat/completions", json=payload
            ) as resp:
                if resp.status_code == 400 and use_tools:
                    body = await resp.aread()
                    if self._maybe_retry_without_tools(resp, body, model):
                        continue
                    raise self._http_error(resp, body)
                if resp.status_code in _TRANSIENT_STATUS and stream_retries < _TRANSIENT_MAX:
                    stream_retries += 1
                    logger.warning(
                        "流式瞬时错误 HTTP %s，%ss 后重试(%s/%s): %s",
                        resp.status_code, _TRANSIENT_BACKOFF, stream_retries, _TRANSIENT_MAX, model,
                    )
                    await asyncio.sleep(_TRANSIENT_BACKOFF)
                    continue
                if resp.status_code >= 400:
                    raise self._http_error(resp, await resp.aread())
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
            return


class ModelRegistry:
    """管理主/视觉/压缩三类端点，支持探测与热刷新。"""

    def __init__(self, settings) -> None:
        self._settings = settings
        self._clients: dict[str, LLMClient] = {}
        self._to_close: list[LLMClient] = []
        self.reload()

    def reload(self) -> None:
        """按当前配置重建端点客户端；被替换的旧客户端延后至下次探测时关闭。"""
        retired = list(self._clients.values())
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
                codex_compat=bool(ep.get("codex_compat", False)),
            )
            new_clients[name] = LLMClient(info)
        self._clients = new_clients
        if retired:
            self._to_close.extend(retired)

    async def probe_all(self) -> None:
        """先关闭被替换的旧客户端，再并发探测当前端点。"""
        to_close, self._to_close = self._to_close, []
        for client in to_close:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass
        await asyncio.gather(*(client.probe() for client in self._clients.values()))

    def get(self, name: str) -> LLMClient | None:
        return self._clients.get(name)

    def any_available(self) -> bool:
        """是否存在至少一个探测成功的端点（就绪探针用）。"""
        return any(c.info.available for c in self._clients.values())

    async def close_all(self) -> None:
        """关闭全部端点连接池（含待回收的旧客户端），进程退出/重载时调用。"""
        current, self._clients = self._clients, {}
        for client in [*current.values(), *self._to_close]:
            try:
                await client.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.debug("关闭端点 %s 连接失败: %s", client.info.name, exc)
        self._to_close.clear()

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
                "codex_compat": ep.codex_compat,
                "detected_modality": ep.detected_modality,
                "available": ep.available,
                "enabled": ep.enabled,
                "models": ep.models,
                "error": ep.error,
                # base_url 可能内嵌凭据（部分中转站用 url 内 api_key），快照中脱敏
                "base_url_masked": _mask_url(ep.base_url),
            }
        return out


def _mask_url(url: str) -> str:
    """脱敏 URL 中可能内嵌的凭据（user:pass@host 或 key 查询参数）。"""
    if not url:
        return ""
    try:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        parts = urlsplit(url)
        netloc = parts.netloc
        if "@" in netloc:
            userinfo, _, host = netloc.rpartition("@")
            name = userinfo.split(":", 1)[0]
            netloc = f"{name}:***@{host}"
        query = [
            (k, "***" if k.lower() in ("key", "api_key", "apikey", "token", "access_token") else v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
        ]
        return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), parts.fragment))
    except Exception:  # noqa: BLE001
        return url
