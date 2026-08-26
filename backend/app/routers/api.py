"""FastAPI 网关 REST 与 WebSocket 端点。

端点：
- GET  /api/health          健康检查（无需鉴权）
- GET  /api/models          端点状态快照
- PUT  /api/models          保存模型配置（热刷新）
- GET  /api/sandbox         沙箱配置/状态
- PUT  /api/sandbox         保存沙箱配置
- GET  /api/skills          Skills 列表
- PUT  /api/skills/{name}   启停 skill
- GET  /api/mcp             MCP server 状态
- GET  /api/models/catalog  模型目录
- POST /api/upload          附件上传
- GET  /api/files/{id}      附件读取
- POST /api/run-code        执行代码（沙箱/local）
- POST /api/compress        手动触发压缩
- GET  /api/sessions        会话列表
- POST /api/sessions        新建会话
- GET  /api/sessions/{id}   读取会话
- DELETE /api/sessions/{id} 清空会话
- POST /api/sessions/{id}/archive  归档会话
- POST /api/auth/login      换取 JWT（无需鉴权）
- WS   /api/chat            对话 WebSocket
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from ..core import auth
from ..services.upload import UploadError, load_object, save_upload

from .deps import compressor, executor, logger, mcp_mgr, pipeline, registry, sessions, settings, skills_mgr, vision

router = APIRouter()


def require_auth(authorization: Annotated[str | None, Header()] = None) -> None:
    """除 health/login 外所有 REST 端点的统一鉴权依赖。"""
    if not auth.auth_enabled():
        return
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    if not auth.check_bearer(token):
        raise HTTPException(status_code=401, detail="未授权")


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


# ---------- REST 端点 ----------
@router.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "mode": settings.sandbox_mode()}


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
        raise HTTPException(status_code=400, detail="缺少 enabled 字段")
    if not skills_mgr.set_enabled(name, bool(payload["enabled"])):
        raise HTTPException(status_code=404, detail=f"skill 不存在: {name}")
    return {"ok": True}


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
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/files/{object_id}")
async def get_file(object_id: str, _: Annotated[None, Depends(require_auth)]) -> Response:
    data = load_object(object_id)
    if data is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    return Response(content=data, media_type="application/octet-stream")


@router.post("/api/run-code")
async def run_code(payload: dict[str, Any], _: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    code = payload.get("code", "")
    if not code.strip():
        raise HTTPException(status_code=400, detail="代码为空")
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
    messages = sessions.load(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail="会话不存在或为空")
    compressed = await compressor.compress(messages)
    sessions.clear(session_id)
    for msg in compressed:
        sessions.append(session_id, msg)
    return {"ok": True, "messages": compressed}


@router.get("/api/sessions")
async def list_sessions(_: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    return {"sessions": sessions.list_sessions()}


@router.post("/api/sessions")
async def create_session(_: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    session_id = sessions.create()
    return {"id": session_id}


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str, _: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    return {"id": session_id, "messages": sessions.load(session_id)}


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, _: Annotated[None, Depends(require_auth)]) -> dict[str, Any]:
    sessions.clear(session_id)
    return {"ok": True}


@router.post("/api/sessions/{session_id}/archive")
async def archive_session(
    session_id: str, _: Annotated[None, Depends(require_auth)]
) -> dict[str, Any]:
    dest = sessions.archive(session_id)
    if dest is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True, "archived": dest.name}


@router.post("/api/sessions/{session_id}/rename")
async def rename_session(
    session_id: str,
    payload: dict[str, Any],
    _: Annotated[None, Depends(require_auth)],
) -> dict[str, Any]:
    try:
        sessions.rename(session_id, payload.get("title", ""))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True, "title": sessions.list_sessions()}


@router.post("/api/auth/login")
async def login(payload: dict[str, Any]) -> dict[str, Any]:
    if not auth.auth_enabled():
        return {"token": auth.create_jwt(), "auth_enabled": False}
    password = payload.get("password", "")
    if password and auth.check_bearer(password):
        return {"token": auth.create_jwt(), "auth_enabled": True}
    if auth.check_bearer(payload.get("api_key", "")):
        return {"token": auth.create_jwt(), "auth_enabled": True}
    raise HTTPException(status_code=401, detail="凭据无效")


# ---------- WebSocket ----------
def _resolve_attachments(attachments: list[Any]) -> list[dict[str, Any]]:
    """将附件引用解析为可送入视觉路由的 image 结构（仅接受图片）。"""
    import base64

    resolved = []
    for att in attachments:
        if isinstance(att, str):
            data = load_object(att)
            if data is None:
                continue
            resolved.append({"type": "image", "object_id": att, "base64": base64.b64encode(data).decode("utf-8")})
        elif isinstance(att, dict) and att.get("type") == "image":
            resolved.append(att)
    return resolved


def _record_event(session_id: str, event: dict[str, Any]) -> None:
    """将 WebSocket 事件同步持久化到会话文件。"""
    etype = event.get("type")
    if etype == "assistant_message":
        sessions.append(session_id, {"role": "assistant", "content": event.get("content", "")})
    elif etype == "thinking":
        sessions.append(session_id, {"role": "thinking", "content": event.get("text", "")})
    elif etype == "tool_call":
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
    elif etype == "delta" and event.get("text"):
        sessions.append(session_id, {"role": "assistant_delta", "content": event.get("text")})


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

    try:
        while True:
            msg = await ws.receive_json()
            msg_type = msg.get("type", "message")
            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
                continue

            # 后端主动推送的消息（欢迎语/通知/提醒）
            if msg_type == "assistant_message":
                content = msg.get("content", "")
                sessions.append(session_id, {"role": "assistant", "content": content})
                await sink({"type": "delta", "text": content})
                await sink({"type": "done"})
                continue

            # 允许前端通过轻量消息切换当前模型（不重新建会话）
            if msg_type == "switch_model":
                model_name = msg.get("model_name") or ""
                if model_name:
                    current_client = registry.get("main")
                    if current_client and current_client.info.model_name != model_name:
                        current_client.info.model_name = model_name
                        await sink({"type": "info", "text": f"已切换到模型: {model_name}"})
                continue

            # 思考强度调整
            if msg_type == "set_thinking":
                level = msg.get("level") or "normal"
                temp_map = {"low": 0.3, "normal": 0.7, "high": 1.0}
                temperature = temp_map.get(level, 0.7)
                await sink({"type": "info", "text": f"思考强度已设为: {level}"})
                continue

            # 保存完整会话历史
            if msg_type == "save_session":
                history = sessions.load(session_id)
                sessions.save(session_id, history)
                continue

            # 重新生成上一条回复：删除最后一个 user 消息之后的全部内容并重新推理
            if msg_type == "regenerate":
                history = sessions.load(session_id)
                idx = None
                for i in range(len(history) - 1, -1, -1):
                    if history[i].get("role") == "user":
                        idx = i
                        break
                if idx is None:
                    await ws.send_json({"type": "error", "message": "没有可重新生成的消息"})
                    continue
                del history[idx + 1 :]
                sessions.save(session_id, history)
                last_user = history[idx]
                resolved = _resolve_attachments(last_user.get("attachments", []))
                sessions.mark_running(session_id)
                try:
                    await pipeline.run_full(history, resolved, sink, force_compress=False)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("重新生成失败")
                    await ws.send_json({"type": "error", "message": str(exc)})
                finally:
                    sessions.mark_completed(session_id)
                continue

            content = msg.get("content", "")
            attachments = msg.get("attachments", [])
            # 仅接受图片附件；object_id 引用已在网关侧解析
            resolved = _resolve_attachments(attachments)

            # 加载历史，追加本轮 user 消息
            history = sessions.load(session_id)
            user_msg: dict[str, Any] = {"role": "user", "content": content}
            if resolved:
                user_msg["attachments"] = [a.get("object_id", "") for a in resolved]
            history.append(user_msg)
            sessions.append(session_id, user_msg)

            force_compress = bool(msg.get("force_compress", False))
            sessions.mark_running(session_id)
            try:
                await pipeline.run_full(history, resolved, sink, force_compress=force_compress)
            except Exception as exc:  # noqa: BLE001
                logger.exception("对话处理失败")
                await ws.send_json({"type": "error", "message": str(exc)})
            finally:
                sessions.mark_completed(session_id)
    except WebSocketDisconnect:
        logger.info("客户端断开连接")
    except Exception as exc:  # noqa: BLE001
        logger.warning("WebSocket 异常: %s", exc)
