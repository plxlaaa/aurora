"""统一异常与错误响应规范。

设计目标：
1. 业务代码抛 `AppError`（含 HTTP 状态码与稳定错误码），由 `main.py` 注册的
   全局异常处理器统一序列化为 JSON，避免每个端点重复 try/except；
2. 错误码（`code`）为机器可读稳定契约，前端与文档均以此为准；
3. `AppError` 继承 FastAPI `HTTPException`，因此对既有调用方与测试完全向后兼容
   （状态码语义、`detail` 字段位置均不变），只是额外附带 `code` / `extra`。

响应格式：
    {"detail": "会话不存在", "code": "session_not_found", "request_id": "..."}
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException


class ErrorCode:
    """稳定错误码常量（前端与文档均以此为准，勿随意改名）。"""

    # 4xx —— 客户端
    VALIDATION_ERROR = "validation_error"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    SESSION_BUSY = "session_busy"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    RATE_LIMITED = "rate_limited"
    UNPROCESSABLE = "unprocessable"

    # 5xx —— 服务端
    INTERNAL_ERROR = "internal_error"
    UPSTREAM_ERROR = "upstream_error"
    SERVICE_UNAVAILABLE = "service_unavailable"


# HTTP 状态码 → 默认错误码（供原生 HTTPException 兜底映射）
STATUS_CODE_MAP: dict[int, str] = {
    400: ErrorCode.VALIDATION_ERROR,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    422: ErrorCode.UNPROCESSABLE,
    429: ErrorCode.RATE_LIMITED,
}


class AppError(HTTPException):
    """业务异常基类。

    直接携带 status_code / detail / headers 语义与 `HTTPException` 一致，
    额外提供稳定的 `code` 与 `extra` 字段供前端分支与日志检索。
    """

    default_code: str = ErrorCode.INTERNAL_ERROR

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        resolved_status = status_code or self.status_code
        super().__init__(status_code=resolved_status, detail=detail, headers=headers)
        self.code = code or self.default_code
        self.extra = extra or {}


# ---------- 4xx 便捷子类 ----------
class ValidationError(AppError):
    """请求参数/载荷不合法（400）。"""

    status_code = 400
    default_code = ErrorCode.VALIDATION_ERROR


class UnauthorizedError(AppError):
    """缺少或无效凭据（401）。"""

    status_code = 401
    default_code = ErrorCode.UNAUTHORIZED


class ForbiddenError(AppError):
    """已认证但无权限（403）。"""

    status_code = 403
    default_code = ErrorCode.FORBIDDEN


class NotFoundError(AppError):
    """资源不存在（404）。"""

    status_code = 404
    default_code = ErrorCode.NOT_FOUND


class ConflictError(AppError):
    """与当前资源状态冲突（409）。"""

    status_code = 409
    default_code = ErrorCode.CONFLICT


class SessionBusyError(ConflictError):
    """会话正在生成中，禁止并发写操作（409）。"""

    default_code = ErrorCode.SESSION_BUSY


class PayloadTooLargeError(AppError):
    """请求体超过限制（413）。"""

    status_code = 413
    default_code = ErrorCode.PAYLOAD_TOO_LARGE


class UnprocessableError(AppError):
    """语义合法但业务上无法处理（422）。"""

    status_code = 422
    default_code = ErrorCode.UNPROCESSABLE


class RateLimitedError(AppError):
    """触发限流（429）。"""

    status_code = 429
    default_code = ErrorCode.RATE_LIMITED


# ---------- 5xx ----------
class InternalError(AppError):
    """未归类的服务端错误（500）。"""

    status_code = 500
    default_code = ErrorCode.INTERNAL_ERROR


class UpstreamError(AppError):
    """上游模型端点异常（502）。"""

    status_code = 502
    default_code = ErrorCode.UPSTREAM_ERROR


class ServiceUnavailableError(AppError):
    """依赖不可用 / 系统繁忙（503）。"""

    status_code = 503
    default_code = ErrorCode.SERVICE_UNAVAILABLE


def error_body(
    detail: str,
    code: str,
    request_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造统一错误响应体（供异常处理器与测试断言复用）。"""
    body: dict[str, Any] = {"detail": detail, "code": code}
    if request_id:
        body["request_id"] = request_id
    if extra:
        body.update(extra)
    return body