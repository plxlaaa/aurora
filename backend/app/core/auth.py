"""简单 JWT 鉴权（文档 10.3）。

开发默认关闭；设置 AI_ASSISTANT_API_KEY 后，网关要求请求携带
Authorization: Bearer <api-key>（Web UI 登录后换取 JWT）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid

API_KEY = os.environ.get("AI_ASSISTANT_API_KEY", "")


def _resolve_secret() -> str:
    """解析 JWT 签名密钥，保证进程重启后已签发的 token 仍然有效。

    优先级：显式配置的 AI_ASSISTANT_JWT_SECRET > 由 API Key 确定性派生 >
    随机值（此时鉴权本就关闭，token 无实际用途）。
    """
    explicit = os.environ.get("AI_ASSISTANT_JWT_SECRET", "")
    if explicit:
        return explicit
    if API_KEY:
        return hashlib.sha256(f"aurora-jwt:{API_KEY}".encode("utf-8")).hexdigest()
    return uuid.uuid4().hex


JWT_SECRET = _resolve_secret()
JWT_TTL = 24 * 3600

# WebSocket 握手与 REST 均走同一检查；JWT 采用 HS256 手写实现，避免额外依赖


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def _sign(payload: str) -> str:
    msg = payload.encode("utf-8")
    digest = hmac.new(JWT_SECRET.encode("utf-8"), msg, hashlib.sha256).digest()
    return _b64url(digest)


def create_jwt(username: str = "user") -> str:
    header = _b64url(b'{"alg":"HS256","typ":"JWT"}')
    body = _b64url(json.dumps({"sub": username, "iat": int(time.time()), "exp": int(time.time()) + JWT_TTL}).encode("utf-8"))
    payload = f"{header}.{body}"
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str) -> dict | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header, body, sig = parts
    payload = f"{header}.{body}"
    if not hmac.compare_digest(_sign(payload), sig):
        return None
    try:
        claims = json.loads(_b64url_decode(body))
    except Exception:  # noqa: BLE001
        return None
    if claims.get("exp", 0) < time.time():
        return None
    return claims


def auth_enabled() -> bool:
    return bool(API_KEY)


def check_bearer(token: str | None) -> bool:
    """REST 请求鉴权：支持 API Key 或 JWT。"""
    if not auth_enabled():
        return True
    if not token:
        return False
    if hmac.compare_digest(token, API_KEY):
        return True
    return verify_token(token) is not None
