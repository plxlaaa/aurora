"""视觉路由决策（文档 5.2）。

依据 vision_routing.policy 在「主模型 / 独立视觉模型」之间选择处理图片的端点。
网关启动与配置热刷新时探测端点模态并缓存。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("aiassistant.vision")


class VisionRouter:
    def __init__(self, settings, registry) -> None:
        self._settings = settings
        self._registry = registry

    def select_model(self) -> tuple[str, Any] | None:
        """返回 (端点名, LLMClient)；无可用视觉能力时返回 None。"""
        cfg = self._settings.endpoints()
        vis = cfg.get("vision")
        vis_enabled = bool(vis and vis.get("enabled", True))
        policy = self._settings.vision_routing().get("policy", "prefer_vision_model")

        main_client = self._registry.get("main")
        vis_client = self._registry.get("vision") if vis_enabled else None
        main_is_mm = self._main_is_multimodal(main_client)

        if policy == "vision_model_only":
            if vis_client is not None:
                return "vision", vis_client
            raise ValueError("视觉路由策略为 vision_model_only，但独立视觉模型未启用")

        if policy == "prefer_main_if_multimodal":
            if main_is_mm and main_client is not None:
                return "main", main_client
            if vis_client is not None:
                return "vision", vis_client
            raise ValueError("主模型非多模态且独立视觉模型不可用")

        # prefer_vision_model（默认）
        if vis_client is not None:
            return "vision", vis_client
        if main_is_mm and main_client is not None:
            return "main", main_client
        raise ValueError("无可用视觉能力：独立视觉模型未启用且主模型非多模态")

    def _main_is_multimodal(self, client) -> bool:
        if client is None:
            return False
        info = client.info
        if info.modality == "multimodal":
            return True
        if info.modality == "text":
            return False
        # auto：以探测结果为准
        return info.detected_modality == "multimodal"

    def attach_image_to_messages(
        self, messages: list[dict[str, Any]], attachments: list[dict[str, Any]]
    ) -> None:
        """将图片附件转换为目标端点的 content 格式（OpenAI 视觉协议）。"""
        if not attachments:
            return
        images = [a for a in attachments if a.get("type") == "image"]
        if not images:
            return
        # 追加到最后一条 user 消息
        last = messages[-1]
        if last.get("role") != "user":
            messages.append({"role": "user", "content": []})
            last = messages[-1]
        content = last.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        elif not isinstance(content, list):
            content = []
        for img in images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": img.get("url")
                        or f"data:{img.get('mime_type', 'image/png')};base64,{img.get('base64', '')}"
                    },
                }
            )
        last["content"] = content
