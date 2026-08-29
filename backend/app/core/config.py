"""配置加载与热刷新。

读取 /opt/aiassistant/config 下的 YAML（models / sandbox / mcp），
提供字典访问与重新加载能力。配置变更后调用 reload() 热刷新。
"""
from __future__ import annotations

import copy
import os
import tempfile
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
        """返回配置段的深拷贝，避免调用方原地修改污染内存配置。"""
        with self._lock:
            return copy.deepcopy(self._data.get(name, {}))

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
        """将配置更新原子写回对应 YAML 并热刷新。

        先写同目录临时文件再 os.replace，避免写入中途失败留下截断的配置文件。
        """
        path = os.path.join(self.config_dir, f"{name}.yaml")
        with self._lock:
            os.makedirs(self.config_dir, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=self.config_dir, prefix=f".{name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_path, path)
            except Exception:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
            self.reload()


settings = Settings()
