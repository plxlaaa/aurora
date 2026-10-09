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
from contextvars import ContextVar
from typing import Any, Callable, Iterable

logger = logging.getLogger("aiassistant.http")

REQUEST_ID_HEADER = "x-request-id"

# 当前请求的 trace id：由 RequestContextMiddleware 绑定，供 JSON 日志格式化器
# 自动附加到每条日志，无需逐处传递参数。
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

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

        # 将 request_id 绑定到上下文，使下游业务日志自动携带同一 trace id
        token = request_id_var.set(request_id)
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # 注意：先记录访问日志再reset 上下文，否则本条日志会丢失 request_id 字段
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
            request_id_var.reset(token)


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
    """对文本类响应做 gzip 压缩，显著降低会话/工具快照的传输体积。

    实现要点（分块响应必须整体压缩后才能声明 content-encoding）：
    响应头 `Content-Encoding: gzip` 一旦发出就代表「整个响应体都是 gzip 流」，
    因此不能只压缩最后一个 chunk——那样客户端按 gzip 解压整条字节流会直接报错，
    大体积静态资源（前端 assets 等多块响应）会整体损坏。

    这里采用「整体缓冲后再决定」策略：
    - 先把响应体缓冲起来，**只有在确认总量落在 [min_size, MAX_BUFFER_BYTES]
      区间内时**才压缩并补gzip 头；
    - 累计量超过 MAX_BUFFER_BYTES（超大文件）立即放弃压缩，转为原样透传，
      绝不把整份大文件读进内存；
    - 已知 Content-Length 时可在收到响应头阶段就提前判定，避免无谓缓冲。

    对 SSE / 图片 / 视频等流式或已压缩类型，本中间件直接跳过。
    """

    # 超过该字节数的响应不再缓冲压缩，直接透传（防止大文件占内存）
    MAX_BUFFER_BYTES = 8 * 1024 * 1024

    def __init__(self, app: Any, *, min_size: int = GZIP_MIN_SIZE) -> None:
        self.app = app
        self.min_size = min_size

    @staticmethod
    def _should_compress(headers: list[tuple[bytes, bytes]], min_size: int = GZIP_MIN_SIZE) -> bool:
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
        return length == 0 or length >= min_size

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

        # 响应头与响应体全部先缓冲，待拿到**完整响应体**后再统一决定压缩与否。
        # 这样「压缩 / 透传」只决策一次，不会出现「已发 gzip 头又发现太大来不及压缩」
        # 或「只压缩了尾块」的半截状态。
        pending_start: dict[str, Any] | None = None
        chunks: list[bytes] = []
        total = 0
        # True=原始透传（已确定不压缩）；False=仍需缓冲等待最终决策
        passthrough = False

        async def _emit(
            start: dict[str, Any], body_chunks: list[bytes], size: int, min_size: int
        ) -> None:
            """按最终决策发出响应头与响应体（整体压缩或原样透传）。"""
            raw = b"".join(body_chunks)
            if size >= min_size:
                compressed = gzip.compress(raw, compresslevel=6)
                headers_out: list[tuple[bytes, bytes]] = [
                    (k, v) for k, v in start.get("headers", [])
                    if k.decode("latin-1").lower() not in ("content-length", "content-encoding")
                ]
                headers_out.append((b"content-encoding", b"gzip"))
                headers_out.append((b"vary", b"accept-encoding"))
                headers_out.append((b"content-length", str(len(compressed)).encode("latin-1")))
                start = {**start, "headers": headers_out}
                await send(start)
                await send({"type": "http.response.body", "body": compressed, "more_body": False})
            else:
                await send(start)
                await send({"type": "http.response.body", "body": raw, "more_body": False})

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal pending_start, total, passthrough, chunks

            if message["type"] == "http.response.start":
                pending_start = message
                eligible = message["status"] < 400 and self._should_compress(
                    message.get("headers", []), self.min_size
                )
                if not eligible:
                    passthrough = True
                else:
                    # 已知 Content-Length 时可提前判定，避免为注定透传的响应做无谓缓冲
                    declared = 0
                    for key, value in message.get("headers", []):
                        if key.decode("latin-1").lower() == "content-length":
                            try:
                                declared = int(value)
                            except ValueError:
                                declared = 0
                    # 不足阈值（压缩无收益）或超出缓冲上限（不应读进内存）→ 直接透传
                    if 0 < declared < self.min_size or declared > self.MAX_BUFFER_BYTES:
                        passthrough = True
                return

            if message["type"] != "http.response.body":
                await send(message)
                return

            body = message.get("body", b"")

            if passthrough:
                # 尚未发出响应头时，先把头补发出去
                if pending_start is not None:
                    start, pending_start = pending_start, None
                    await send(start)
                await send(message)
                return

            chunks.append(body)
            total += len(body)
            # 超大响应：放弃压缩，转为原样透传。
            # 注意必须把已缓冲的分块一并 flush 出去，否则这些字节会被丢弃（数据丢失）。
            if total > self.MAX_BUFFER_BYTES:
                passthrough = True
                more = bool(message.get("more_body"))
                if pending_start is not None:
                    start, pending_start = pending_start, None
                    await send(start)
                # 已缓冲内容 + 当前块合并为一块发出，后续块走透传分支
                await send({"type": "http.response.body", "body": b"".join(chunks), "more_body": more})
                chunks, total = [], 0
                return
            # 收到结束块 → 缓冲完整，统一决策并发出
            if pending_start is not None and not message.get("more_body"):
                start, pending_start = pending_start, None
                await _emit(start, chunks, total, self.min_size)
                chunks, total = [], 0

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # 收尾：应用未发结束块（如抛异常中断）时把已缓冲内容刷出，
            # 避免请求停在「头未发」或「体未发」的半截状态。
            if pending_start is not None or chunks:
                start = pending_start or {"type": "http.response.start", "status": 200, "headers": []}
                await _emit(start, chunks, total, self.min_size)


def install(app: Any, *, max_body_bytes: int, slow_request_ms: int = 30_000) -> None:
    """按执行顺序注册全部中间件（后注册者更外层）。"""
    # 注册顺序决定包裹层次：GZip 最外层最先看到响应头
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=max_body_bytes)
    app.add_middleware(GZipMiddleware, min_size=GZIP_MIN_SIZE)
    app.add_middleware(RequestContextMiddleware, slow_request_ms=slow_request_ms)


def slow_request_threshold(default: int) -> int:
    """便于测试覆盖的阈值读取。"""
    return default


def iter_header_items(headers: Iterable[tuple[bytes, bytes]]) -> dict[str, str]:
    """字节头转小写字典（供测试与工具函数复用）。"""
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in headers}