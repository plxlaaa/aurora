"""运行时环境配置（环境变量集中解析）。

与业务配置（config/*.yaml）分离：本模块只管「进程怎么跑」——监听地址、
CORS 白名单、请求体上限、日志级别等，便于容器/systemd/Windows 服务统一注入。

所有变量均支持默认值，缺省即可零配置启动（开发模式）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_list(name: str, default: tuple[str, ...] = ()) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class RuntimeConfig:
    """进程级运行参数快照（构造后不可变）。"""

    host: str = field(default_factory=lambda: os.environ.get("AI_ASSISTANT_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("AI_ASSISTANT_PORT", 8080))

    # CORS：默认仅允许同源（前后端同源部署由 nginx/静态托管承担，无需放开）
    cors_origins: list[str] = field(
        default_factory=lambda: _env_list("AI_ASSISTANT_CORS_ORIGINS", ("http://localhost:5173",))
    )
    cors_allow_credentials: bool = field(
        default_factory=lambda: _env_bool("AI_ASSISTANT_CORS_CREDENTIALS", False)
    )

    # 请求体上限（需 >= 单个附件最大值，默认 24MB 覆盖 10MB 图片的 base64 膨胀）
    max_body_bytes: int = field(
        default_factory=lambda: _env_int("AI_ASSISTANT_MAX_BODY_MB", 24) * 1024 * 1024
    )

    # 并发生成上限（保护模型端点）
    max_concurrent_generations: int = field(
        default_factory=lambda: _env_int("AI_ASSISTANT_MAX_CONCURRENT_GENERATIONS", 3)
    )

    # 静态前端目录：存在则挂载到根路径，实现单进程一体化部署（免 nginx）
    frontend_dir: str = field(
        default_factory=lambda: os.environ.get("AI_ASSISTANT_FRONTEND_DIR", "")
    )

    # 生产模式：开启后 OpenAPI 文档与 CORS 收紧（由部署脚本设置）
    production: bool = field(
        default_factory=lambda: _env_bool("AI_ASSISTANT_PRODUCTION", False)
    )

    log_level: str = field(default_factory=lambda: os.environ.get("AI_ASSISTANT_LOG_LEVEL", "INFO").upper())
    log_json: bool = field(default_factory=lambda: _env_bool("AI_ASSISTANT_LOG_JSON", False))

    # 慢请求告警阈值（毫秒）
    slow_request_ms: int = field(default_factory=lambda: _env_int("AI_ASSISTANT_SLOW_REQUEST_MS", 30_000))

    # 是否信任反向代理的 X-Forwarded-*（nginx 在前时开启，影响日志 IP 记录）
    trust_proxy: bool = field(default_factory=lambda: _env_bool("AI_ASSISTANT_TRUST_PROXY", True))

    @property
    def docs_url(self) -> str | None:
        """生产模式下关闭 Swagger UI，避免暴露接口结构。"""
        return None if self.production else "/docs"

    @property
    def openapi_url(self) -> str | None:
        return None if self.production else "/openapi.json"

    @property
    def enable_docs(self) -> bool:
        return not self.production


runtime = RuntimeConfig()