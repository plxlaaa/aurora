#!/usr/bin/env bash
# 本地开发启动脚本：以后端默认配置目录运行
# 用法：bash dev.sh  （需先按 README 安装依赖）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export AI_ASSISTANT_CONFIG_DIR="$ROOT/config"
export AI_ASSISTANT_DATA_DIR="$ROOT/data"
export AI_ASSISTANT_SKILLS_DIR="$ROOT/config/skills"
export AI_ASSISTANT_UPLOAD_DIR="$ROOT/data/uploads"
export AI_ASSISTANT_SANDBOX_LOCAL_DIR="$ROOT/sandbox"

mkdir -p "$ROOT"/data/sessions "$ROOT"/data/uploads "$ROOT"/sandbox

cd "$ROOT/backend"
exec venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080
