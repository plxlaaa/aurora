"""配置加载与热刷新。

读取 /opt/aiassistant/config 下的 YAML（models / sandbox / mcp），
提供字典访问与重新加载能力。配置变更后调用 reload() 热刷新。
"""
from __future__ import annotations

import os
import threading
from typing import Any

import yaml

CONFIG_DIR = os.environ.get("AI_ASSISTANT_CONFIG_DIR", "/opt/aiassistant/config")


class Settings:
    def __init__(self, config_dir: str = CONFIG_DIR) -> None:
        self.config_dir = config_dir
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, Any]] = {}
        self.reload()

    def reload(self) -> None:
        with self._lock:
            loaded: dict[str, dict[str, Any]] = {}
            for name in ("models", "sandbox", "mcp"):
                path = os.path.join(self.config_dir, f"{name}.yaml")
                if os.path.exists(path):
                    with open(path, encoding="utf-8") as fh:
                        data = yaml.safe_load(fh) or {}
                    loaded[name] = data if isinstance(data, dict) else {}
                else:
                    loaded[name] = {}
            self._data = loaded

    # -- 通用访问 --
    def raw(self, name: str) -> dict[str, Any]:
        with self._lock:
            return self._data.get(name, {})

    # -- 模型配置 --
    def endpoints(self) -> dict[str, Any]:
        return self.raw("models").get("endpoints", {})

    def vision_routing(self) -> dict[str, Any]:
        return self.raw("models").get("vision_routing", {})

    def compress(self) -> dict[str, Any]:
        return self.raw("models").get("compress", {})

    # -- 沙箱配置 --
    def sandbox(self) -> dict[str, Any]:
        return self.raw("sandbox")

    def sandbox_mode(self) -> str:
        return self.sandbox().get("mode", "sandbox")

    # -- MCP 配置 --
    def mcp_servers(self) -> list[dict[str, Any]]:
        return self.raw("mcp").get("servers", [])

    def update(self, name: str, data: dict[str, Any]) -> None:
        """将内存中的配置更新写回对应 YAML 文件并热刷新。"""
        path = os.path.join(self.config_dir, f"{name}.yaml")
        with self._lock:
            with open(path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
            self.reload()


settings = Settings()
