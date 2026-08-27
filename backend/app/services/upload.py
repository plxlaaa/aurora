"""附件上传（文档 9.4）。

POST /api/upload 保存文件并分配 object_id；图片保存原始文件，
网关侧将 base64 内容注入消息以供视觉路由使用。
"""
from __future__ import annotations

import base64
import logging
import os
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("aiassistant.upload")

UPLOAD_DIR = os.environ.get("AI_ASSISTANT_UPLOAD_DIR", "/opt/aiassistant/data/uploads")

# 允许的类型与 mime 映射
ALLOWED_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_SIZE = 10 * 1024 * 1024  # 10MB


class UploadError(Exception):
    pass


# 图片魔数 → mime，用于 content_type 缺失或不准确时兜底识别
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_mime(data: bytes) -> str | None:
    """按魔数识别图片 mime；无法识别时返回 None。"""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def save_upload(filename: str, content_type: str, data: bytes) -> dict[str, Any]:
    if not data:
        raise UploadError("文件内容为空")
    if len(data) > MAX_SIZE:
        raise UploadError(f"文件超过大小限制 {MAX_SIZE // (1024 * 1024)}MB")

    # 以实际内容为准，避免客户端 content_type 不准确导致误判
    mime = sniff_mime(data) or content_type
    if mime not in ALLOWED_EXT:
        raise UploadError(f"不支持的文件类型: {content_type or '未知'}")

    upload_dir = Path(UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    object_id = uuid.uuid4().hex[:24]
    ext = ALLOWED_EXT[mime]
    path = upload_dir / f"{object_id}{ext}"
    path.write_bytes(data)

    return {
        "object_id": object_id,
        "filename": filename,
        "mime_type": mime,
        "size": len(data),
        "url": f"/api/files/{object_id}",
        "base64": base64.b64encode(data).decode("utf-8"),
    }


def _resolve_path(object_id: str) -> Path | None:
    safe = "".join(c for c in object_id if c.isalnum() or c in "-_")
    if not safe:
        return None
    upload_dir = Path(UPLOAD_DIR)
    for ext in ALLOWED_EXT.values():
        path = upload_dir / f"{safe}{ext}"
        if path.exists():
            return path
    return None


def load_object(object_id: str) -> bytes | None:
    path = _resolve_path(object_id)
    return path.read_bytes() if path is not None else None


def load_object_with_mime(object_id: str) -> tuple[bytes, str] | None:
    """读取附件内容并返回真实 mime，供视觉协议与文件下载使用。"""
    path = _resolve_path(object_id)
    if path is None:
        return None
    data = path.read_bytes()
    mime = sniff_mime(data) or _EXT_TO_MIME.get(path.suffix, "application/octet-stream")
    return data, mime


_EXT_TO_MIME = {ext: mime for mime, ext in ALLOWED_EXT.items()}
