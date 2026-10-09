"""FastAPI 网关 REST 与 WebSocket 端点。

端点：
- GET  /api/health          健康检查（无需鉴权）
- GET  /api/models          端点状态快照
- PUT  /api/models          保存模型配置（热刷新）
- GET  /api/sandbox         沙箱配置/状态
- PUT  /api/sandbox         保存沙箱配置
- GET  /api/tools           工具中心快照（内置/skill/custom 分组）
- GET/POST  /api/tools/custom          自定义工具列表 / 创建
- GET/PUT/DELETE /api/tools/custom/{name} 详情 / 更新 / 删除
- PUT  /api/tools/custom/{name}/enabled 启停
- POST /api/tools/custom/{name}/test   测试运行
- GET  /api/skills          Skills 列表
- PUT  /api/skills/{name}   启停 skill
- POST /api/skills/upload   zip 导入 skill
- GET  /api/skills/{name}/files      文件树
- GET/PUT /api/skills/{name}/files/{path} 读 / 保存文本文件
- DELETE /api/skills/{name} 删除 skill
- GET  /api/mcp             MCP server 状态
- GET/POST /api/mcp/servers        server 配置列表 / 新增
- PUT/DELETE /api/mcp/servers/{name} 更新（含重连）/ 删除
- POST /api/mcp/servers/{name}/reconnect 手动重连
- GET  /api/models/catalog  模型目录
- POST /api/upload          附件上传
- GET  /api/files/{id}      附件读取
- POST /api/run-code        执行代码（沙箱/local）
- POST /api/compress        手动触发压缩
- GET  /api/sessions        会话列表
- POST /api/sessions        新建会话
- GET  /api/sessions/{id}   读取会话
- DELETE /api/sessions/{id} 删除会话
- POST /api/sessions/{id}/clear    清空会话消息
- POST /api/sessions/{id}/archive  归档会话
- POST /api/sessions/{id}/rename   重命名会话
- POST /api/auth/login      换取 JWT（无需鉴权）
- WS   /api/chat            对话 WebSocket
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections import OrderedDict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from ..core import auth
from ..core.errors import (
    ErrorCode,
    NotFoundError,
    RateLimitedError,
    SessionBusyError,
    UnauthorizedError,
    ValidationError,
)
from ..core.runtime import runtime
from ..services.upload import UploadError, load_object_with_mime, save_upload

from .deps import (
    compressor,
    custom_tools,
    executor,
    hooks,
    logger,
    mcp_mgr,
    pipeline,
    plugins,
    readiness,
    registry,
    sessions,
    settings,
    skills_mgr,
    tool_registry,
    vision,
)

router = APIRouter()

# 思考强度 → 采样温度
THINKING_TEMPERATURE = {"low": 0.3, "normal": 0.7, "high": 1.0}

# 会话列表分页上限（防止一次返回过多数据拖慢前端）
MAX_PAGE_SIZE = 200


def require_auth(authorization: Annotated[str | None, Header()] = None) -> None:
    """除 health/login 外所有 REST 端点的统一鉴权依赖。"""
    if not auth.auth_enabled():
        return
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    if not auth.check_bearer(token):
        raise UnauthorizedError("未授权")


# ---------- 全局生成并发上限 ----------
# 信号量限制同时在跑的生成任务数，防多连接无上限并发压垮模型端点。
# 上限可通过 AI_ASSISTANT_MAX_CONCURRENT_GENERATIONS 调整。
_generation_slots = asyncio.Semaphore(runtime.max_concurrent_generations)


def _reject_busy_session(session_id: str) -> None:
    """写操作与生成中会话互斥：压缩/清空/删除与流式落盘并发会写坏会话文件。"""
    if sessions.get_status(session_id) == sessions.STATUS_RUNNING:
        raise SessionBusyError("会话正在生成中，请稍后再试")


def _merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """递归合并：patch 中为 None 的字段忽略，嵌套 dict 逐层合并，其余字段覆盖。"""
    merged = dict(base)
    for key, value in patch.items():
        if value is None:
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _require_dict(payload: Any, what: str = "请求体") -> dict[str, Any]:
    """校验请求体为 JSON 对象；数组/标量等给出明确 400 而非 500。"""
    if not isinstance(payload, dict):
        raise ValidationError(f"{what}必须为 JSON 对象")
    return payload


# ---------- REST 端点 ----------
@router.get("/api/health")
async def health() -> dict[str, Any]:
    """存活 + 就绪探针（无需鉴权）。

    - liveness：进程可响应即 ok；
    - ready：至少一个模型端点探测成功（无端点配置时视为就绪，便于纯配置阶段）。
    """
    info = readiness()
    return {
        "ok": True,
        "status": "healthy",
        "ready": info["ready"],
        "mode": settings.sandbox_mode(),
        "version": info["version"],
        "endpoints_available": info["endpoints_available"],
        "endpoints_total": info["endpoints_total"],
        "uptime_hint": "see /api/stats",
    }


@router.get("/api/ready")
async def ready() -> dict[str, Any]:
    """就绪详情（运维/监控用）：端点、MCP、插件、鉴权状态。"""
    info = readiness()
    return {"ok": True, **info}


@router.get("/api/models")
async def list_models(_: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    await registry.probe_all()
    return {
        "endpoints": registry.snapshot(),
        "vision_routing": settings.vision_routing(),
        "compress": settings.compress(),
    }


@router.put("/api/models")
async def update_models(
    payload: dict[str, Any], _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    current = settings.raw("models")
    # 字段级合并：仅覆盖请求中显式提供的字段，未提供的（如 api_key）保留原值
    if "endpoints" in payload:
        current["endpoints"] = _merge_dict(current.get("endpoints", {}), payload["endpoints"])
    if "vision_routing" in payload:
        current["vision_routing"] = _merge_dict(current.get("vision_routing", {}), payload["vision_routing"])
    if "compress" in payload:
        current["compress"] = _merge_dict(current.get("compress", {}), payload["compress"])
    settings.update("models", current)
    registry.reload()
    await registry.probe_all()
    return {"ok": True, "endpoints": registry.snapshot()}


@router.get("/api/sandbox")
async def get_sandbox(_: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    return settings.sandbox()


@router.put("/api/sandbox")
async def update_sandbox(
    payload: dict[str, Any], _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    settings.update("sandbox", payload)
    return {"ok": True, "sandbox": settings.sandbox()}


@router.get("/api/skills")
async def list_skills(_: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    return {"skills": skills_mgr.list()}


@router.put("/api/skills/{name}")
async def toggle_skill(
    name: str, payload: dict[str, Any], _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    if "enabled" not in payload:
        raise ValidationError("缺少 enabled 字段")
    if not skills_mgr.set_enabled(name, bool(payload["enabled"])):
        raise NotFoundError(f"skill 不存在: {name}")
    tool_registry.invalidate_tools_cache()
    return {"ok": True}


@router.delete("/api/skills/{name}")
async def delete_skill(name: str, _: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    if not skills_mgr.delete(name):
        raise NotFoundError(f"skill 不存在: {name}")
    tool_registry.invalidate_tools_cache()
    return {"ok": True}


@router.post("/api/skills/upload")
async def upload_skill(
    file: UploadFile, _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    data = await file.read()
    try:
        result = skills_mgr.import_zip(data)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    tool_registry.invalidate_tools_cache()
    return {"ok": True, **result}


@router.get("/api/skills/{name}/files")
async def skill_files(name: str, _: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    try:
        return {"files": skills_mgr.list_files(name)}
    except KeyError as exc:
        raise NotFoundError(str(exc)) from exc


@router.get("/api/skills/{name}/files/{path:path}")
async def skill_read_file(
    name: str, path: str, _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    try:
        return {"path": path, "content": skills_mgr.read_file(name, path)}
    except KeyError as exc:
        raise NotFoundError(str(exc)) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise ValidationError(str(exc)) from exc


@router.put("/api/skills/{name}/files/{path:path}")
async def skill_write_file(
    name: str, path: str, payload: dict[str, Any], _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    content = payload.get("content")
    if not isinstance(content, str):
        raise ValidationError("缺少 content 字段")
    try:
        skills_mgr.write_file(name, path, content)
    except KeyError as exc:
        raise NotFoundError(str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise ValidationError(str(exc)) from exc
    return {"ok": True}


# ---------- 工具中心 ----------
@router.get("/api/tools")
async def list_tools(_: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    return tool_registry.snapshot()


@router.get("/api/tools/custom")
async def list_custom_tools(_: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    return {"tools": custom_tools.list()}


@router.get("/api/tools/custom/{name}")
async def get_custom_tool(
    name: str, _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    tool = custom_tools.get(name)
    if tool is None:
        raise NotFoundError(f"工具不存在: {name}")
    return {"tool": tool}


def _validate_custom_name(name: str) -> None:
    if name in ("read_skill", "run_skill", "run_code") or name.startswith("mcp_"):
        raise ValidationError(f"工具名与内置/MCP 命名空间冲突: {name}")
    if tool_registry.name_exists(name):
        raise ValidationError(f"工具名已存在: {name}")


@router.post("/api/tools/custom")
async def create_custom_tool(
    payload: dict[str, Any], _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    name = str(payload.get("name", ""))
    _validate_custom_name(name)
    try:
        custom_tools.save(payload)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    tool_registry.invalidate_tools_cache()
    return {"ok": True, "name": name}


@router.put("/api/tools/custom/{name}")
async def update_custom_tool(
    name: str, payload: dict[str, Any], _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    if custom_tools.get(name) is None:
        raise NotFoundError(f"工具不存在: {name}")
    payload["name"] = name
    try:
        custom_tools.save(payload, overwrite=True)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    tool_registry.invalidate_tools_cache()
    return {"ok": True}


@router.delete("/api/tools/custom/{name}")
async def delete_custom_tool(
    name: str, _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    if not custom_tools.delete(name):
        raise NotFoundError(f"工具不存在: {name}")
    tool_registry.invalidate_tools_cache()
    return {"ok": True}


@router.put("/api/tools/custom/{name}/enabled")
async def toggle_custom_tool(
    name: str, payload: dict[str, Any], _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    if "enabled" not in payload:
        raise ValidationError("缺少 enabled 字段")
    if not custom_tools.set_enabled(name, bool(payload["enabled"])):
        raise NotFoundError(f"工具不存在: {name}")
    tool_registry.invalidate_tools_cache()
    return {"ok": True}


@router.post("/api/tools/custom/{name}/test")
async def test_custom_tool(
    name: str, payload: dict[str, Any], _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    tool = custom_tools.get(name)
    if tool is None:
        raise NotFoundError(f"工具不存在: {name}")
    arguments = payload.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ValidationError("arguments 必须为对象")
    if tool["type"] == "http":
        result = await custom_tools._run_http(tool, arguments)
    else:
        result = await custom_tools._run_python(tool, arguments)
    return {"result": result}


# ---------- MCP server 管理 ----------
@router.get("/api/mcp/servers")
async def list_mcp_servers(_: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    return {"servers": settings.mcp_servers()}


def _validate_mcp_cfg(cfg: dict[str, Any]) -> None:
    name = str(cfg.get("name", "")).strip()
    if not name:
        raise ValidationError("缺少 name 字段")
    transport = cfg.get("transport", "stdio")
    if transport not in ("stdio", "http", "sse"):
        raise ValidationError(f"不支持的 transport: {transport}")
    if transport == "stdio" and not str(cfg.get("command", "")).strip():
        raise ValidationError("stdio 传输必须提供 command")
    if transport in ("http", "sse") and not str(cfg.get("url", "")).strip():
        raise ValidationError(f"{transport} 传输必须提供 url")
    cfg["name"] = name


@router.post("/api/mcp/servers")
async def create_mcp_server(
    payload: dict[str, Any], _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    _validate_mcp_cfg(payload)
    name = payload["name"]
    servers = settings.mcp_servers()
    if any(s.get("name") == name for s in servers):
        raise ValidationError(f"MCP server 已存在: {name}")
    servers.append(payload)
    settings.update("mcp", {"servers": servers})
    mcp_mgr.upsert(payload)
    tool_registry.invalidate_tools_cache()
    status = await mcp_mgr.connect_one(name)
    return {"ok": True, "status": status}


@router.put("/api/mcp/servers/{name}")
async def update_mcp_server(
    name: str, payload: dict[str, Any], _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    servers = settings.mcp_servers()
    idx = next((i for i, s in enumerate(servers) if s.get("name") == name), None)
    if idx is None:
        raise NotFoundError(f"MCP server 不存在: {name}")
    payload["name"] = name
    _validate_mcp_cfg(payload)
    servers[idx] = payload
    settings.update("mcp", {"servers": servers})
    mcp_mgr.upsert(payload)
    tool_registry.invalidate_tools_cache()
    status = await mcp_mgr.connect_one(name) if payload.get("enabled", True) else f"{name}(已禁用)"
    return {"ok": True, "status": status}


@router.delete("/api/mcp/servers/{name}")
async def delete_mcp_server(
    name: str, _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    servers = settings.mcp_servers()
    remaining = [s for s in servers if s.get("name") != name]
    if len(remaining) == len(servers):
        raise NotFoundError(f"MCP server 不存在: {name}")
    settings.update("mcp", {"servers": remaining})
    mcp_mgr.remove(name)
    tool_registry.invalidate_tools_cache()
    return {"ok": True}


@router.post("/api/mcp/servers/{name}/reconnect")
async def reconnect_mcp_server(
    name: str, _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    if not any(s.get("name") == name for s in settings.mcp_servers()):
        raise NotFoundError(f"MCP server 不存在: {name}")
    if mcp_mgr.get_handle(name) is None:
        for cfg in settings.mcp_servers():
            if cfg.get("name") == name:
                mcp_mgr.upsert(cfg)
                break
    status = await mcp_mgr.connect_one(name)
    return {"ok": True, "status": status}


@router.get("/api/mcp")
async def list_mcp(_: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    return {"servers": mcp_mgr.list()}


@router.get("/api/models/catalog")
async def model_catalog(_: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    raw = settings.raw("models")
    return {
        "endpoints": registry.snapshot(),
        "catalogs": raw.get("catalogs", {}),
    }


@router.put("/api/models/catalog")
async def save_model_catalog(
    payload: dict[str, Any], _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    current = settings.raw("models")
    current["catalogs"] = payload.get("catalogs", current.get("catalogs", {}))
    settings.update("models", current)
    registry.reload()
    return {"ok": True, "catalogs": current.get("catalogs", {})}


@router.post("/api/upload")
async def upload_file(
    file: UploadFile, _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    content_type = file.content_type or "application/octet-stream"
    data = await file.read()
    try:
        return save_upload(file.filename or "upload", content_type, data)
    except UploadError as exc:
        raise ValidationError(str(exc))


@router.get("/api/files/{object_id}")
async def get_file(object_id: str, _: Annotated[None, Depends(require_auth)]) -> Response:
    found = load_object_with_mime(object_id)
    if found is None:
        raise NotFoundError("文件不存在")
    data, mime = found
    return Response(content=data, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})


@router.post("/api/run-code")
async def run_code(payload: dict[str, Any], _: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    code = payload.get("code", "")
    if not code.strip():
        raise ValidationError("代码为空")
    result = await executor.execute(code)
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "mode": result.mode,
    }


@router.post("/api/compress")
async def manual_compress(
    payload: dict[str, Any], _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    session_id = payload.get("session_id", "")
    _reject_busy_session(session_id)
    messages = sessions.load(session_id)
    if not messages:
        raise NotFoundError("会话不存在或为空")
    # 压缩前先把流式碎片合并，避免摘要里出现大量 assistant_delta 片段
    sessions.collapse_deltas(session_id)
    compressed = await compressor.compress(sessions.load(session_id))
    sessions.save(session_id, compressed)
    return {"ok": True, "messages": compressed}


@router.get("/api/sessions")
async def list_sessions(
    _: Annotated[None, Depends(require_auth)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description="返回条数上限")] = 50,
    offset: Annotated[int, Query(ge=0, description="跳过的条数")] = 0,
    q: Annotated[str, Query(max_length=100, description="按标题/内容关键字过滤")] = "",
) -> dict[str, Any]:
    """会话列表（按最后更新时间倒序），支持分页与关键字过滤。

    会话累积较多时，前端应使用 limit/offset 分页，避免一次性拉全量。
    """
    page, total = sessions.list_sessions_page(limit=limit, offset=offset, keyword=q.strip())
    return {
        "sessions": page,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(page) < total,
    }


@router.post("/api/sessions")
async def create_session(_: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    session_id = sessions.create()
    return {"id": session_id}


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str, _: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    if not sessions.exists(session_id):
        raise NotFoundError("会话不存在")
    return {
        "id": session_id,
        "messages": sessions.load(session_id),
        "status": sessions.get_status(session_id),
    }


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, _: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    _reject_busy_session(session_id)
    if not sessions.delete(session_id):
        raise NotFoundError("会话不存在")
    return {"ok": True}


@router.post("/api/sessions/{session_id}/clear")
async def clear_session(session_id: str, _: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    _reject_busy_session(session_id)
    if not sessions.exists(session_id):
        raise NotFoundError("会话不存在")
    sessions.clear(session_id)
    return {"ok": True}


@router.post("/api/sessions/{session_id}/archive")
async def archive_session(
    session_id: str, _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    _reject_busy_session(session_id)
    dest = sessions.archive(session_id)
    if dest is None:
        raise NotFoundError("会话不存在")
    return {"ok": True, "archived": dest.name}


@router.post("/api/sessions/{session_id}/rename")
async def rename_session(
    session_id: str,
    payload: dict[str, Any],
    _: Annotated[None, Depends(require_auth)],
) -> dict[str, Any]:
    title = payload.get("title", "")
    try:
        sessions.rename(session_id, title)
    except FileNotFoundError:
        raise NotFoundError("会话不存在")
    return {"ok": True, "id": session_id, "title": title}


@router.get("/api/sessions/{session_id}/export")
async def export_session(
    session_id: str,
    format: str = "md",
    _: Annotated[None, Depends(require_auth)] = None,
) -> Response:
    """导出会话为 Markdown 或 JSON 文件。"""
    import json as _json
    if format not in ("md", "json"):
        raise ValidationError("format 仅支持 md 或 json")
    if not sessions.exists(session_id):
        raise NotFoundError("会话不存在")
    sessions.collapse_deltas(session_id)
    messages = sessions.load(session_id)
    if not messages:
        raise NotFoundError("会话不存在或为空")
    # 定点取元信息，避免为导出单个会话而全量扫描所有会话文件
    meta = sessions.get_meta(session_id) or {}
    title = str(meta.get("title") or session_id)
    from urllib.parse import quote
    ascii_name = "".join(c for c in title if c.isascii() and (c.isalnum() or c in "-_ "))[:40].strip() or "session"
    utf8_name = quote("".join(c for c in title if c.isalnum() or c in "-_ ")[:40].strip() or session_id)
    if format == "json":
        content = _json.dumps(
            {"id": session_id, "title": title, "messages": messages},
            ensure_ascii=False, indent=2,
        )
        return Response(
            content=content, media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}.json\"; filename*=UTF-8''{utf8_name}.json"},
        )
    # Markdown 导出：按角色排版，工具调用与思考过程折叠
    lines: list[str] = [f"# {title}", ""]
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content") or ""
        if role == "user":
            lines += ["## 🧑 用户", "", content, ""]
        elif role == "assistant":
            # 剔除文本协议调用标记（调用过程以工具行呈现）
            import re as _re
            content = _re.sub(r"<\s*tool_?call\s*>[\s\S]*?<\s*/\s*tool_?call\s*>", "", content)
            lines += ["## 🤖 助手", "", content, ""]
        elif role == "tool":
            lines += [f"> 🔧 工具 `{msg.get('name', '')}` 结果：", ">", f"> {content}", ""]
        elif role == "tool_call":
            args = _json.dumps(msg.get("arguments") or {}, ensure_ascii=False)
            result = msg.get("result")
            result_text = _json.dumps(result, ensure_ascii=False) if result is not None else ""
            lines += [
                f"> 🔧 调用 `{msg.get('name', '')}` 参数：`{args}`",
                (f"> 结果：`{result_text}`" if result_text else ""),
                "",
            ]
        elif role == "thinking":
            lines += ["<details><summary>💭 思考过程</summary>", "", content, "", "</details>", ""]
    text = "\n".join(lines)
    return Response(
        content=text, media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}.md\"; filename*=UTF-8''{utf8_name}.md"},
    )


# ---------- 登录速率限制（防口令爆破） ----------
_LOGIN_ATTEMPT_MAX = 5        # 窗口内最大失败次数
_LOGIN_WINDOW = 60.0          # 失败计数窗口（秒）
_LOGIN_LOCKOUT = 300.0        # 触发上限后的锁定时长（秒）
_login_failures: dict[str, list[float]] = {}  # ip -> 失败时间戳
# 失败表上限：防止伪造大量来源 IP 造成内存无界增长
_LOGIN_TRACK_MAX = 10_000
# 上次清理过期条目的时间戳：避免每次失败都全表扫描
_login_last_sweep = 0.0
# 清理扫描的最小间隔（秒）
_LOGIN_SWEEP_INTERVAL = 60.0


def _client_ip(request: Request) -> str:
    """取真实来源 IP。

    nginx/IIS 等反向代理下 request.client 是代理地址，需优先取
    X-Forwarded-For 首段（最靠近客户端的一跳）；仅在信任代理时采信该头，
    避免攻击者直接伪造该头绕过限流。
    """
    if runtime.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip
    return request.client.host if request.client else "unknown"


def _sweep_login_failures(now: float) -> None:
    """周期性清理已过期的失败记录（限频执行，避免每次登录都全表扫描）。

    登录是低频操作，但攻击者可高频调用；若无界增长会持续占用内存。
    这里按固定间隔做一次全表清理，把「锁定窗口」之外的条目尽早回收。
    """
    global _login_last_sweep
    if now - _login_last_sweep < _LOGIN_SWEEP_INTERVAL:
        return
    _login_last_sweep = now
    cutoff = now - _LOGIN_LOCKOUT
    expired = [ip for ip, ts in _login_failures.items() if not any(t > cutoff for t in ts)]
    for ip in expired:
        _login_failures.pop(ip, None)
    if expired:
        logger.debug("清理过期登录失败记录 %d 条", len(expired))


def _login_guard(ip: str) -> None:
    """超限则拒绝；失败记录自动过期（按锁定窗口过滤）。"""
    now = time.monotonic()
    recent = [t for t in _login_failures.get(ip, []) if now - t < _LOGIN_LOCKOUT]
    _login_failures[ip] = recent
    # 仅统计计数窗口内的失败
    if sum(1 for t in recent if now - t < _LOGIN_WINDOW) >= _LOGIN_ATTEMPT_MAX:
        raise RateLimitedError("尝试过于频繁，请 5 分钟后再试")
    _sweep_login_failures(now)


def _record_login_failure(ip: str) -> None:
    """登记一次失败；表满时清理最早过期的条目防无界增长。"""
    _login_failures.setdefault(ip, []).append(time.monotonic())
    if len(_login_failures) > _LOGIN_TRACK_MAX:
        cutoff = time.monotonic() - _LOGIN_LOCKOUT
        for key in [k for k, v in _login_failures.items() if not any(t > cutoff for t in v)]:
            _login_failures.pop(key, None)


@router.post("/api/auth/login")
async def login(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """凭据换取 JWT。失败按来源 IP 计数，超过阈值锁定 5 分钟。"""
    ip = _client_ip(request)
    if not auth.auth_enabled():
        return {"token": auth.create_jwt(), "auth_enabled": False}
    _login_guard(ip)
    password = payload.get("password", "")
    if password and auth.check_bearer(password):
        _login_failures.pop(ip, None)
        return {"token": auth.create_jwt(), "auth_enabled": True}
    if auth.check_bearer(payload.get("api_key", "")):
        _login_failures.pop(ip, None)
        return {"token": auth.create_jwt(), "auth_enabled": True}
    _record_login_failure(ip)
    logger.warning("登录失败 ip=%s", ip)
    # 凭据错误统一回同一提示，避免暴露「用户名/密钥格式」等细节
    raise UnauthorizedError("凭据无效")


# ---------- 统计 ----------
@router.get("/api/stats")
async def get_stats(_: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    """用量统计快照（内置 stats 插件）+ 钩子订阅清单。"""
    stats = plugins.get("stats")
    return {
        "stats": stats.snapshot() if stats else {},
        "hooks": hooks.snapshot(),
    }


# ---------- WebSocket ----------
# 附件数据缓存（object_id -> 原始字节）；LRU 上限防内存泄漏
_ATTACH_CACHE_MAX = 32
_attach_data_cache: OrderedDict[str, tuple[bytes, str]] = OrderedDict()


def _cache_attach(object_id: str, data: bytes, mime: str) -> None:
    _attach_data_cache[object_id] = (data, mime)
    _attach_data_cache.move_to_end(object_id)
    while len(_attach_data_cache) > _ATTACH_CACHE_MAX:
        _attach_data_cache.popitem(last=False)


def _resolve_attachments(attachments: list[Any]) -> list[dict[str, Any]]:
    """将附件引用解析为可送入视觉路由的 image 结构（仅接受图片）。"""
    resolved: list[dict[str, Any]] = []
    for att in attachments:
        object_id = att if isinstance(att, str) else None
        if isinstance(att, dict) and not att.get("base64"):
            # 前端只给了引用（object_id/id），同样需要网关侧读盘
            object_id = att.get("object_id") or att.get("id")
        if object_id:
            if object_id not in _attach_data_cache:
                found = load_object_with_mime(object_id)
                if found is None:
                    continue
                data, mime = found
                _cache_attach(object_id, data, mime)
            data, mime = _attach_data_cache[object_id]
            resolved.append({
                "type": "image",
                "object_id": object_id,
                "mime_type": mime,
                "base64": base64.b64encode(data).decode("utf-8"),
            })
            continue
        elif isinstance(att, dict) and att.get("type") == "image":
            resolved.append(att)
    return resolved


def _record_event(session_id: str, event: dict[str, Any]) -> None:
    """将 WebSocket 事件同步持久化到会话文件。"""
    etype = event.get("type")
    if etype == "assistant_message":
        sessions.append(session_id, {"role": "assistant", "content": event.get("content", "")})
    elif etype == "thinking":
        sessions.append_stream(session_id, "thinking", event.get("text", ""))
    elif etype == "tool_call":
        # running 状态不落盘，避免与 done/error 重复记录
        if event.get("status") == "running":
            return
        sessions.append(session_id, {
            "role": "tool_call",
            "name": event.get("name"),
            "arguments": event.get("arguments"),
            "result": event.get("result"),
            "status": event.get("status"),
        })
    elif etype == "tool":
        sessions.append(session_id, {
            "role": "tool",
            "tool_call_id": event.get("tool_call_id"),
            "name": event.get("name"),
            "content": event.get("content"),
        })
    elif etype == "tool_result":
        # 文本协议工具结果：以 user 消息形态落盘，与 pipeline 内部历史一致
        sessions.append(session_id, {
            "role": "user",
            "content": (
                f'<tool_result name="{event.get("name", "")}">'
                f'{event.get("result_text", "")}</tool_result>\n'
                "请根据工具结果继续。"
            ),
        })
    elif etype == "delta" and event.get("text"):
        # 流式增量合并进末尾同 role 记录，避免 JSONL 行数爆炸
        sessions.append_stream(session_id, "assistant_delta", event.get("text", ""))


@router.websocket("/api/chat")
async def chat(ws: WebSocket) -> None:
    await ws.accept()
    # 鉴权：可传 Authorization 头或查询参数 token
    auth_header = ws.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        auth_header = auth_header[7:]
    token = ws.query_params.get("token") or auth_header
    if auth.auth_enabled() and not auth.check_bearer(token):
        await ws.send_json({"type": "error", "message": "未授权"})
        await ws.close(code=4401)
        return

    session_id = ws.query_params.get("session_id", "")
    if not session_id:
        session_id = sessions.create()
        # 新会话主动推送欢迎语
        welcome = "你好！我是 AI 智能助手。有什么可以帮你的吗？你可以直接问我问题，也可以上传图片让我帮你分析。"
        await ws.send_json({"type": "assistant_message", "content": welcome})
        sessions.append(session_id, {"role": "assistant", "content": welcome})

    async def sink(event: dict[str, Any]) -> None:
        await ws.send_json(event)
        _record_event(session_id, event)

    # 连接局部状态：模型与温度只影响本连接，不污染全局 registry
    conn_model: str | None = None
    conn_temperature = THINKING_TEMPERATURE["normal"]
    current_task: asyncio.Task[None] | None = None
    cancel_event: asyncio.Event | None = None

    async def generate(history: list[dict[str, Any]], resolved: list[dict[str, Any]],
                       force_compress: bool, cancel: asyncio.Event) -> None:
        """在独立任务中执行一轮生成，便于接收循环并发处理 cancel 指令。"""
        sessions.mark_running(session_id)
        try:
            await pipeline.run_full(
                history,
                resolved,
                sink,
                session_id=session_id,
                force_compress=force_compress,
                temperature=conn_temperature,
                model_name=conn_model,
                cancel=cancel,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("对话处理失败")
            await hooks.emit("generate.error", {"session_id": session_id, "error": str(exc)})
            try:
                await ws.send_json({"type": "error", "message": str(exc)})
            except Exception:  # noqa: BLE001
                pass
        finally:
            # 把本轮流式碎片合并为单条 assistant 消息后再落状态
            sessions.collapse_deltas(session_id)
            sessions.mark_completed(session_id)

    async def _guarded_generate(*args, **kwargs):
        """占用全局生成槽位执行；槽位耗尽时排队会被调用前的 locked() 检查拦截。"""
        async with _generation_slots:
            return await generate(*args, **kwargs)

    def busy() -> bool:
        return current_task is not None and not current_task.done()

    # 心跳：客户端需周期性发送 {"type":"ping"}，服务端回{"type":"pong"}。
    # 超过 WS_IDLE_TIMEOUT 秒未收到任何消息即判定连接已断开，主动回收，
    # 避免客户端静默掉线（网络中断/页面关闭）后连接与生成任务长期残留。
    WS_IDLE_TIMEOUT = 300.0

    async def receive_with_heartbeat() -> dict[str, Any]:
        """带空闲超时的接收：超时则关闭连接并抛出以跳出主循环。"""
        return await asyncio.wait_for(ws.receive_json(), timeout=WS_IDLE_TIMEOUT)

    try:
        while True:
            try:
                msg = await receive_with_heartbeat()
            except asyncio.TimeoutError:
                logger.info("WebSocket 空闲超时（%.0fs 无心跳），主动关闭连接", WS_IDLE_TIMEOUT)
                try:
                    await ws.close(code=1001)
                except Exception:  # noqa: BLE001
                    pass
                break
            msg_type = msg.get("type", "message")
            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
                continue

            # 中断当前生成：置位事件，由 pipeline 在下一个 chunk 边界停止
            if msg_type in ("cancel", "stop"):
                if cancel_event is not None and busy():
                    cancel_event.set()
                else:
                    await ws.send_json({"type": "cancelled"})
                continue

            # 后端主动推送的消息（欢迎语/通知/提醒）
            if msg_type == "assistant_message":
                content = msg.get("content", "")
                sessions.append(session_id, {"role": "assistant", "content": content})
                await ws.send_json({"type": "delta", "text": content})
                await ws.send_json({"type": "done"})
                continue

            # 允许前端通过轻量消息切换当前模型（仅作用于本连接）
            if msg_type == "switch_model":
                model_name = msg.get("model_name") or ""
                if model_name and model_name != conn_model:
                    conn_model = model_name
                    await ws.send_json({"type": "info", "text": f"已切换到模型: {model_name}"})
                continue

            # 思考强度调整 → 采样温度（仅作用于本连接）
            if msg_type == "set_thinking":
                level = msg.get("level") or "normal"
                conn_temperature = THINKING_TEMPERATURE.get(level, THINKING_TEMPERATURE["normal"])
                await ws.send_json({"type": "info", "text": f"思考强度已设为: {level}"})
                continue

            # 保存完整会话历史
            if msg_type == "save_session":
                sessions.collapse_deltas(session_id)
                continue

            if msg_type in ("message", "regenerate") and busy():
                await ws.send_json({"type": "error", "message": "上一轮生成尚未结束"})
                continue

            # 重新生成上一条回复：删除最后一个 user 消息之后的全部内容并重新推理
            if msg_type == "regenerate":
                sessions.collapse_deltas(session_id)
                history = sessions.load(session_id)
                idx = None
                for i in range(len(history) - 1, -1, -1):
                    msg_i = history[i]
                    # 跳过工具结果包装的 user 消息：重放「请根据工具结果继续」
                    # 会缺失工具调用上下文导致模型混乱，应定位到原始提问
                    if msg_i.get("role") == "user" and not str(
                        msg_i.get("content") or ""
                    ).startswith("<tool_result"):
                        idx = i
                        break
                if idx is None:
                    await ws.send_json({"type": "error", "message": "没有可重新生成的消息"})
                    continue
                del history[idx + 1 :]
                sessions.save(session_id, history)
                resolved = _resolve_attachments(history[idx].get("attachments", []))
                cancel_event = asyncio.Event()
                if _generation_slots.locked():
                    await ws.send_json({"type": "error", "message": "系统生成任务已达上限，请稍后再试"})
                    continue
                current_task = asyncio.create_task(
                    _guarded_generate(history, False, cancel_event)
                )
                continue

            content = msg.get("content", "")
            attachments = msg.get("attachments", [])
            # 仅接受图片附件；object_id 引用已在网关侧解析
            resolved = _resolve_attachments(attachments)
            if not content.strip() and not resolved:
                await ws.send_json({"type": "error", "message": "消息内容为空"})
                continue

            # 加载历史，追加本轮 user 消息
            sessions.collapse_deltas(session_id)
            history = sessions.load(session_id)
            user_msg: dict[str, Any] = {"role": "user", "content": content}
            if resolved:
                user_msg["attachments"] = [a.get("object_id", "") for a in resolved]
            history.append(user_msg)
            sessions.append(session_id, user_msg)

            cancel_event = asyncio.Event()
            if _generation_slots.locked():
                await ws.send_json({"type": "error", "message": "系统生成任务已达上限，请稍后再试"})
                continue
            current_task = asyncio.create_task(
                _guarded_generate(history, resolved, bool(msg.get("force_compress", False)), cancel_event)
            )
    except WebSocketDisconnect:
        logger.info("客户端断开连接")
    except Exception as exc:  # noqa: BLE001
        logger.warning("WebSocket 异常: %s", exc)
    finally:
        # 连接结束时终止仍在跑的生成任务，避免向已关闭的 socket 写入
        if current_task is not None and not current_task.done():
            if cancel_event is not None:
                cancel_event.set()
            current_task.cancel()
            try:
                await current_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        sessions.collapse_deltas(session_id)
