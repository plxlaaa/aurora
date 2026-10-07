"""日志初始化：文本与 JSON 双格式。

`AI_ASSISTANT_LOG_JSON=true` 时输出结构化 JSON 行，便于 Loki / Elasticsearch
等日志系统采集与检索；默认沿用人类可读的文本格式。

JSON 行固定包含 `ts` / `level` / `logger` / `msg` 四个字段，并自动附加当前
请求的 `request_id`（由 `core.middleware.request_id_var` 绑定），使访问日志与
业务日志可按同一 trace id 串联。

用法（`core.deps` 导入即生效）：
    from .core.logging import setup_logging
    setup_logging(level="INFO", json_output=True)
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from typing import Any

# 固定字段：跨行统一，避免采集侧按 logger 名猜测
_RESERVED = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
) | {"message", "asctime", "taskName"}

TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class JsonFormatter(logging.Formatter):
    """将日志记录序列化为单行 JSON。"""

    def format(self, record: logging.LogRecord) -> str:
        #延迟导入，避免 core.logging 与 core.middleware 的循环导入
        from .middleware import request_id_var

        payload: dict[str, Any] = {
            "ts": dt.datetime.fromtimestamp(record.created, tz=dt.timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id

        # 业务自定义字段（logger.info(..., extra={"session_id": ...})）
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(
    *,
    level: str = "INFO",
    json_output: bool = False,
    stream: Any = None,
) -> None:
    """配置根日志器（幂等：重复调用只替换 handler，不重复叠加）。

    level/json_output 通常由 `core.runtime` 从环境变量解析后传入。
    """
    handler = logging.StreamHandler(stream or sys.stderr)
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(TEXT_FORMAT))

    root = logging.getLogger()
    resolved = getattr(logging, str(level).upper(), logging.INFO)
    root.setLevel(resolved)
    # 替换而非追加：uvicorn reload 或多次导入时不产生重复日志行
    for existing in list(root.handlers):
        if getattr(existing, "_aurora_handler", False):
            root.removeHandler(existing)
    handler._aurora_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)

    # uvicorn 自带 handler 会造成每条日志重复输出，这里交由根日志器统一接管
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
