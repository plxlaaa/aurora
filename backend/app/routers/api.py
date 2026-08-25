"""FastAPI 网关 REST 与 WebSocket 端点。

端点：
- GET  /api/health          健康检查
- GET  /api/models          端点状态快照
- PUT  /api/models          保存模型配置（热刷新）
- GET  /api/sandbox         沙箱配置/状态
- PUT  /api/sandbox         保存沙箱配置
- GET  /api/skills          Skills 列表
- PUT  /api/skills/{name}   启停 skill
- GET  /api/mcp             MCP server 状态
- POST /api/upload          附件上传
- GET  /api/files/{id}      附件读取
- POST /api/run-code        执行代码（沙箱/local）
- POST /api/compress        手动触发压缩
- GET  /api/sessions        会话列表
- DELETE /api/sessions/{id} 清空会话
- WS   /api/chat            对话 WebSocket
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from ..core import auth
from ..services.skills import SKILLS_DIR
from ..services.upload import UploadError, load_object, save_upload

from .deps import compressor, executor, logger, mcp_mgr, pipeline, registry, sessions, settings, skills_mgr, vision

router = APIRouter()


def _check_auth(authorization: str | None) -> None:
    if not auth.auth_enabled():
        return
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    if not auth.check_bearer(token):
        raise HTTPException(status_code=401, detail="未授权")


# ---------- REST 端点 ----------
@router.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "mode": settings.sandbox_mode()}


@router.get("/api/models")
async def list_models(authorization: str | None = None) -> dict[str, Any]:
    _check_auth(authorization)
    await registry.probe_all()
    return {
        "endpoints": registry.snapshot(),
        "vision_routing": settings.vision_routing(),
        "compress": settings.compress(),
    }


@router.put("/api/models")
async def update_models(payload: dict[str, Any], authorization: str | None = None) -> dict[str, Any]:
    _check_auth(authorization)
    current = settings.raw("models")
    if "endpoints" in payload:
        current["endpoints"] = payload["endpoints"]
    if "vision_routing" in payload:
        current["vision_routing"] = payload["vision_routing"]
    if "compress" in payload:
        current["compress"] = payload["compress"]
    settings.update("models", current)
    registry.reload()
    await registry.probe_all()
    return {"ok": True, "endpoints": registry.snapshot()}


@router.get("/api/sandbox")
async def get_sandbox(authorization: str | None = None) -> dict[str, Any]:
    _check_auth(authorization)
    return settings.sandbox()


@router.put("/api/sandbox")
async def update_sandbox(payload: dict[str, Any], authorization: str | None = None) -> dict[str, Any]:
    _check_auth(authorization)
    settings.update("sandbox", payload)
    return {"ok": True, "sandbox": settings.sandbox()}


@router.get("/api/skills")
async def list_skills(authorization: str | None = None) -> dict[str, Any]:
    _check_auth(authorization)
    return {"skills": skills_mgr.list()}


@router.put("/api/skills/{name}")
async def toggle_skill(name: str, payload: dict[str, Any], authorization: str | None = None) -> dict[str, Any]:
    _check_auth(authorization)
    import os
    from pathlib import Path

    import yaml

    from ..services.skills import SKILLS_DIR

    manifest_path = None
    for p in Path(SKILLS_DIR).glob("*/manifest.yaml"):
        with open(p, encoding="utf-8") as fh:
            manifest = yaml.safe_load(fh) or {}
        if manifest.get("name") == name:
            manifest_path = p
            break
    if manifest_path is None:
        raise HTTPException(status_code=404, detail=f"skill 不存在: {name}")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = yaml.safe_load(fh) or {}
    if "enabled" in payload:
        manifest["enabled"] = payload["enabled"]
    with open(manifest_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(manifest, fh, allow_unicode=True, sort_keys=False)
    skills_mgr.reload()
    return {"ok": True}


@router.get("/api/mcp")
async def list_mcp(authorization: str | None = None) -> dict[str, Any]:
    _check_auth(authorization)
    return {"servers": mcp_mgr.list()}


@router.get("/api/models/catalog")
async def model_catalog(authorization: str | None = None) -> dict[str, Any]:
    raw = settings.raw("models")
    return {
        "endpoints": registry.snapshot(),
        "catalogs": raw.get("catalogs", {}),
    }


@router.put("/api/models/catalog")
async def save_model_catalog(payload: dict[str, Any], authorization: str | None = None) -> dict[str, Any]:
    current = settings.raw("models")
    current["catalogs"] = payload.get("catalogs", current.get("catalogs", {}))
    settings.update("models", current)
    registry.reload()
    return {"ok": True, "catalogs": current.get("catalogs", {})}


@router.post("/api/upload")
async def upload_file(file: UploadFile, authorization: str | None = None) -> dict[str, Any]:
    _check_auth(authorization)
    content_type = file.content_type or "application/octet-stream"
    data = await file.read()
    try:
        return save_upload(file.filename or "upload", content_type, data)
    except UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/files/{object_id}")
async def get_file(object_id: str, authorization: str | None = None) -> Response:
    _check_auth(authorization)
    data = load_object(object_id)
    if data is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    return Response(content=data, media_type="application/octet-stream")


@router.post("/api/run-code")
async def run_code(payload: dict[str, Any], authorization: str | None = None) -> dict[str, Any]:
    _check_auth(authorization)
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
async def manual_compress(payload: dict[str, Any], authorization: str | None = None) -> dict[str, Any]:
    _check_auth(authorization)
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
async def list_sessions(authorization: str | None = None) -> dict[str, Any]:
    return {"sessions": sessions.list_sessions()}


@router.post("/api/sessions")
async def create_session(authorization: str | None = None) -> dict[str, Any]:
    session_id = sessions.create()
    return {"id": session_id}


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str, authorization: str | None = None) -> dict[str, Any]:
    return {"id": session_id, "messages": sessions.load(session_id)}


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str, authorization: str | None = None) -> dict[str, Any]:
    sessions.clear(session_id)
    return {"ok": True}


@router.post("/api/sessions/{session_id}/archive")
async def archive_session(session_id: str, authorization: str | None = None) -> dict[str, Any]:
    path = sessions._path(session_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="会话不存在")
    archive_dir = sessions.data_dir / "archive"
    archive_dir.mkdir(exist_ok=True)
    dest = archive_dir / path.name
    path.replace(dest)
    return {"ok": True, "archived": dest.name}


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

            content = msg.get("content", "")
            attachments = msg.get("attachments", [])
            # 仅接受图片附件；object_id 引用已在网关侧解析
            resolved = []
            for att in attachments:
                if isinstance(att, str):
                    data = load_object(att)
                    if data is None:
                        continue
                    resolved.append({"type": "image", "object_id": att, "base64": __import__("base64").b64encode(data).decode("utf-8")})
                elif isinstance(att, dict) and att.get("type") == "image":
                    resolved.append(att)

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