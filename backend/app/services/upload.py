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


def save_upload(filename: str, content_type: str, data: bytes) -> dict[str, Any]:
    if content_type not in ALLOWED_EXT:
        raise UploadError(f"不支持的文件类型: {content_type}")
    if len(data) > MAX_SIZE:
        raise UploadError(f"文件超过大小限制 {MAX_SIZE // (1024 * 1024)}MB")

    upload_dir = Path(UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    object_id = uuid.uuid4().hex[:24]
    ext = ALLOWED_EXT[content_type]
    path = upload_dir / f"{object_id}{ext}"
    path.write_bytes(data)

    return {
        "object_id": object_id,
        "filename": filename,
        "mime_type": content_type,
        "url": f"/api/files/{object_id}",
        "base64": base64.b64encode(data).decode("utf-8"),
    }


def load_object(object_id: str) -> bytes | None:
    safe = "".join(c for c in object_id if c.isalnum() or c in "-_")
    upload_dir = Path(UPLOAD_DIR)
    for ext in ALLOWED_EXT.values():
        path = upload_dir / f"{safe}{ext}"
        if path.exists():
            return path.read_bytes()
    return None
