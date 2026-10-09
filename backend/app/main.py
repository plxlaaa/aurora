"""FastAPI 网关入口（文档 10.1）。

职责：
- 创建 ASGI 应用并挂载中间件链（请求上下文/安全头/请求体限额/gzip）；
- 注册统一异常处理器（`AppError` / 请求校验失败 / 未捕获异常 → 规范 JSON）；
- 挂载 REST + WebSocket 路由；
- 可选挂载前端静态产物（`AI_ASSISTANT_FRONTEND_DIR`），实现单进程一体化部署。
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .core import middleware as mw
from .core.errors import STATUS_CODE_MAP, AppError, ErrorCode, error_body
from .core.runtime import runtime
from .routers import deps
from .routers.api import router

logger = logging.getLogger("aiassistant.gateway")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    started = time.perf_counter()
    await deps.startup()
    logger.info("网关启动完成（耗时 %.0fms）", (time.perf_counter() - started) * 1000)
    yield
    await deps.shutdown()
    logger.info("网关已优雅关闭")


def _create_app() -> FastAPI:
    app = FastAPI(
        title="AI Assistant Gateway",
        version="1.3.0",
        description=(
            "单机 AI 智能助手网关：多轮对话、视觉理解、上下文压缩、"
            "Skills / MCP / 自定义工具、会话管理与流式输出。"
        ),
        lifespan=lifespan,
        docs_url=runtime.docs_url,
        redoc_url=None if runtime.production else "/redoc",
        openapi_url=runtime.openapi_url,
    )

    # ---- 中间件：注册顺序与执行顺序相反，越晚注册越外层 ----
    # CORS 白名单化：默认仅放行本机开发端口；生产由 nginx 同源代理，无需放开
    app.add_middleware(
        CORSMiddleware,
        allow_origins=runtime.cors_origins,
        allow_credentials=runtime.cors_allow_credentials,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    mw.install(
        app,
        max_body_bytes=runtime.max_body_bytes,
        slow_request_ms=runtime.slow_request_ms,
    )

    # ---- 统一异常处理 ----
    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.detail, exc.code, _request_id(request), exc.extra),
            headers=exc.headers,
        )

    @app.exception_handler(HTTPException)
    async def _http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        # 保持 FastAPI 原生语义，仅补齐 code / request_id 字段
        code = STATUS_CODE_MAP.get(exc.status_code, ErrorCode.VALIDATION_ERROR)
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(detail, code, _request_id(request)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query"))
        msg = first.get("msg", "参数校验失败")
        detail = f"{loc}: {msg}" if loc else msg
        logger.info("参数校验失败 %s %s -> %s", request.method, request.url.path, detail)
        return JSONResponse(
            status_code=422,
            content=error_body(detail, ErrorCode.VALIDATION_ERROR, _request_id(request),
                               {"errors": [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()[:10]]}),
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        # 兜底：记录完整堆栈（含 request_id 便于定位），对外不暴露内部细节
        logger.exception("未处理异常 rid=%s %s %s", _request_id(request), request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_body("服务器内部错误", ErrorCode.INTERNAL_ERROR, _request_id(request)),
        )

    app.include_router(router)
    _mount_frontend(app)
    return app


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _mount_frontend(app: FastAPI) -> None:
    """挂载前端静态产物（可选）。

    设置 AI_ASSISTANT_FRONTEND_DIR 后，网关直接托管 Vue 产物，
    Windows/Linux 均可「单进程一体化」部署，无需额外 nginx。
    """
    dist = runtime.frontend_dir.strip()
    if not dist:
        return
    root = Path(dist).expanduser()
    index = root / "index.html"
    if not index.is_file():
        logger.warning("AI_ASSISTANT_FRONTEND_DIR=%s 未找到 index.html，跳过静态挂载", dist)
        return

    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    # 解析后的根目录：后续所有路径边界判断均以此为基准（resolve 可消除符号链接）
    resolved_root = root.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        """静态文件优先，未命中回退 index.html（支撑 Vue Router history 模式）。"""
        if full_path.startswith(("api/", "ws")):
            return JSONResponse(status_code=404, content=error_body("接口不存在", ErrorCode.NOT_FOUND))
        # 防路径穿越：仅允许 dist 目录内的文件。
        # 用 is_relative_to 做「路径分量级」判断，而非字符串前缀匹配——
        # 后者会把同级兄弟目录（如 dist-secrets/）误判为在 dist 内。
        if full_path:
            try:
                candidate = (root / full_path).resolve()
                candidate.relative_to(resolved_root)
            except (ValueError, OSError):
                # 越界或非法路径：记录并回退到 index.html，不泄漏目录外内容
                logger.warning("拒绝越界的静态路径请求: %s", full_path)
                candidate = None
            if candidate is not None and candidate.is_file():
                return FileResponse(str(candidate))
        return FileResponse(str(index))

    logger.info("已挂载前端静态目录: %s", root)


app = _create_app()