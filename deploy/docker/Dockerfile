# =============================================================================
# 多阶段构建：前端产物 + Python 后端，运行镜像仅含运行时所需
# =============================================================================
# 构建：docker build -f deploy/docker/Dockerfile -t aurora-gateway:1.1.0 .
# 运行：docker run -d --name aurora -p 8080:8080 -v /srv/aurora/config:/app/config aurora-gateway:1.1.0
# =============================================================================

# ---------- 阶段 1：构建前端 ----------
FROM node:20-alpine AS frontend

WORKDIR /build

# 先拷贝 lockfile 与清单以利用 Docker 层缓存（依赖不变时跳过 npm ci）
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


# ---------- 阶段 2：构建后端依赖 ----------
FROM python:3.11-slim AS backend-deps

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# gcc 仅用于编译个别依赖的 sdist，编译后由 --no-compile 清理
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-compile -r requirements.txt


# ---------- 阶段 3：运行时 ----------
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="Aurora AI Assistant Gateway" \
      org.opencontainers.image.description="单机 AI 智能助手网关（FastAPI + Vue 3）" \
      org.opencontainers.image.version="1.1.0" \
      org.opencontainers.image.source="https://github.com/plxlaaa/aurora"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    AI_ASSISTANT_CONFIG_DIR=/app/config \
    AI_ASSISTANT_DATA_DIR=/app/data/sessions \
    AI_ASSISTANT_UPLOAD_DIR=/app/data/uploads \
    AI_ASSISTANT_SKILLS_DIR=/app/config/skills \
    AI_ASSISTANT_FRONTEND_DIR=/app/static \
    AI_ASSISTANT_HOST=0.0.0.0 \
    AI_ASSISTANT_PORT=8080

# curl 用于容器健康检查；tini 负责转发信号，保证 uvicorn 能优雅退出
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --home-dir /app --shell /usr/sbin/nologin aurora

COPY --from=backend-deps /opt/venv /opt/venv
COPY backend/ /app/backend/
COPY config/ /app/config/
COPY --from=frontend /build/dist/ /app/static/

# 数据目录属主对齐非 root 运行用户
RUN mkdir -p /app/data/sessions /app/data/uploads /app/logs \
    && chown -R aurora:aurora /app

USER aurora
WORKDIR /app/backend

EXPOSE 8080

# 使用 /api/ready：能区分「进程存活」与「模型端点就绪」
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]