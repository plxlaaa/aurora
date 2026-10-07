"""HTTP 中间件集合。

按执行顺序（外层 → 内层）：

1. `RequestContextMiddleware` —— 分配/透传 `X-Request-ID`，记录耗时与状态码；
2. `SecurityHeadersMiddleware` —— 统一安全响应头；
3. `BodySizeLimitMiddleware` —— 拒绝超大请求体（413），防内存打满；
4. `GZipMiddleware` —— 对可压缩响应做 gzip（事件流/WebSocket 不受影响）。

说明：纯 ASGI 实现，不依赖 `BaseHTTPMiddleware`，避免其已知性能与
流式响应限制问题；WebSocket 升级请求自动跳过。
"""
from __future__ import annotations

import gzip
import json
import logging
import time
import uuid
from typing import Any, Callable, Iterable

logger = logging.getLogger("aiassistant.http")

REQUEST_ID_HEADER = "x-request-id"

# 低于该字节数的响应不压缩（压缩收益抵不过 CPU 开销）
GZIP_MIN_SIZE = 1024
# 不压缩的 Content-Type（已是压缩格式或流式）
GZIP_SKIP_CONTENT_TYPES = (
    "text/event-stream",
    "application/x-ndjson",
    "image/",
    "video/",
    "audio/",
    "application/zip",
    "application/gzip",
)


def _client_ip(scope: dict[str, Any]) -> str:
    """取客户端 IP：优先反向代理透传的 X-Forwarded-For 首段。"""
    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
    forwarded = headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


class RequestContextMiddleware:
    """注入 request_id 上下文并输出结构化访问日志。"""

    def __init__(self, app: Any, *, slow_request_ms: int = 30_000) -> None:
        self.app = app
        self.slow_request_ms = slow_request_ms

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        # 复用上游传入的 request id，便于跨服务链路追踪
        request_id = headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        status_holder: dict[str, int] = {}
        started = time.perf_counter()

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                raw = message.setdefault("headers", [])
                raw.append((REQUEST_ID_HEADER.encode("latin-1"), request_id.encode("latin-1")))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            status = status_holder.get("status", 0)
            # 健康检查属高频噪声，降级为 debug
            log = logger.debug if scope.get("path") == "/api/health" else logger.info
            log(
                "%s %s -> %s %.0fms rid=%s ip=%s",
                scope.get("method", "-"), scope.get("path", "-"),
                status, elapsed_ms, request_id, _client_ip(scope),
            )
            if elapsed_ms > self.slow_request_ms:
                logger.warning(
                    "慢请求 %.0fms %s %s rid=%s", elapsed_ms, scope.get("method"), scope.get("path"), request_id
                )


class SecurityHeadersMiddleware:
    """统一附加安全响应头（不覆盖已有同名头）。"""

    HEADERS: tuple[tuple[str, str], ...] = (
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "SAMEORIGIN"),
        ("referrer-policy", "strict-origin-when-cross-origin"),
        ("x-xss-protection", "0"),
    )

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        existing = {k.decode("latin-1").lower() for k, _ in scope.get("headers", [])}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                raw = message.setdefault("headers", [])
                present = {k.decode("latin-1").lower() for k, _ in raw}
                for name, value in self.HEADERS:
                    if name not in existing and name not in present:
                        raw.append((name.encode("latin-1"), value.encode("latin-1")))
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BodySizeLimitMiddleware:
    """限制请求体大小，超限直接 413，避免大包打满内存。"""

    def __init__(self, app: Any, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 先看 Content-Length（大多数上传/JSON 请求都有），可无开销快速拒绝
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(send, int(declared))
            return

        received = 0

        async def receive_wrapper() -> dict[str, Any]:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # chunked 传输长度未知，只能边收边判定；中断下游并由外层统一回 413
                    raise _TooLarge()
            return message

        try:
            await self.app(scope, receive_wrapper, send)
        except _TooLarge:
            await self._reject(send, received)

    async def _reject(self, send: Any, size: int) -> None:
        from .errors import ErrorCode, error_body

        limit_mb = self.max_bytes // (1024 * 1024)
        body = json.dumps(
            error_body(f"请求体超过 {limit_mb}MB 限制", ErrorCode.PAYLOAD_TOO_LARGE),
            ensure_ascii=False,
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("latin-1")),
                (b"connection", b"close"),
            ],
        })
        await send({"type": "http.response.body", "body": body})


class _TooLarge(Exception):
    """内部信号：chunked 请求体累计超限。"""


class GZipMiddleware:
    """对文本类响应做 gzip 压缩，显著降低会话/工具快照的传输体积。"""

    def __init__(self, app: Any, *, min_size: int = GZIP_MIN_SIZE) -> None:
        self.app = app
        self.min_size = min_size

    @staticmethod
    def _should_compress(headers: list[tuple[bytes, bytes]]) -> bool:
        length = 0
        content_type = ""
        already = False
        for key, value in headers:
            k = key.decode("latin-1").lower()
            if k == "content-length":
                try:
                    length = int(value)
                except ValueError:
                    length = 0
            elif k == "content-type":
                content_type = value.decode("latin-1").lower()
            elif k == "content-encoding":
                already = True
        if already:
            return False
        if any(content_type.startswith(skip) for skip in GZIP_SKIP_CONTENT_TYPES):
            return False
        # 无 Content-Length（分块/流式）也压缩，收益通常更大
        return length == 0 or length >= GZIP_MIN_SIZE

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # 客户端不支持或已有 Accept-Encoding 冲突时不处理
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        accept = headers.get("accept-encoding", "")
        if "gzip" not in accept.lower():
            await self.app(scope, receive, send)
            return

        state: dict[str, Any] = {"compress": False, "started": False}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": list(message.get("headers", []))}
                if message["status"] < 400 and self._should_compress(message["headers"]):
                    raw: list[tuple[bytes, bytes]] = [
                        (k, v) for k, v in message["headers"]
                        if k.decode("latin-1").lower() not in ("content-length", "content-encoding")
                    ]
                    raw.append((b"content-encoding", b"gzip"))
                    raw.append((b"vary", b"accept-encoding"))
                    message["headers"] = raw
                    state["compress"] = True
                state["started"] = True
                await send(message)
                return

            if message["type"] == "http.response.body" and state.get("compress"):
                body = message.get("body", b"")
                more = message.get("more_body", False)
                # 最后一个非空块且整体足够大才压缩；空 body 直接透传
                if not more and body and len(body) >= self.min_size:
                    compressed = gzip.compress(body, compresslevel=6)
                    message = {
                        **message,
                        "body": compressed,
                        "headers": [(k, v) for k, v in message.get("headers", [])]
                        + [(b"content-length", str(len(compressed)).encode("latin-1"))],
                    }
                    await send(message)
                    return
            await send(message)

        await self.app(scope, receive, send_wrapper)


def install(app: Any, *, max_body_bytes: int) -> None:
    """按执行顺序注册全部中间件（后注册者更外层）。"""
    # 注册顺序决定包裹层次：GZip 最外层最先看到响应头
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=max_body_bytes)
    app.add_middleware(GZipMiddleware, min_size=GZIP_MIN_SIZE)
    app.add_middleware(RequestContextMiddleware)


def slow_request_threshold(default: int) -> int:
    """便于测试覆盖的阈值读取。"""
    return default


def iter_header_items(headers: Iterable[tuple[bytes, bytes]]) -> dict[str, str]:
    """字节头转小写字典（供测试与工具函数复用）。"""
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in headers}