#!/usr/bin/env bash
# =============================================================================
# package-tarball.sh —— 构建 Linux 离线安装包（tar.gz）
#
# 产出dist/aurora-<version>-linux-<arch>.tar.gz，内含：
#   backend/            后端源码 + 预建虚拟环境（可选--with-venv）
#   frontend/dist/      前端构建产物（已由 npm run build 生成）
#   config/             配置模板（不含任何密钥）
#   deploy/             systemd unit / nginx / 环境变量模板
#   install-tarball.sh  一键安装脚本（无需 git / npm / 网络）
#   SHA256SUMS          校验文件
#
# 适用于：内网/离线环境、无systemd 的容器、或需要灰度分发的场景。
# 常规联网安装请优先使用 deploy/install.sh。
#
# 用法：
#   bash scripts/package-tarball.sh                     # 打包（不含 venv）
#   bash scripts/package-tarball.sh --with-venv         # 预建 venv，目标机免编译
#   bash scripts/package-tarball.sh --with-frontend     # 先执行 npm run build
#   bash scripts/package-tarball.sh --version 1.2.0     # 指定版本号
#   bash scripts/package-tarball.sh --outdir /tmp/pkg   # 指定输出目录
# =============================================================================
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="1.3.0"
WITH_VENV=0
WITH_FRONTEND=0
OUTDIR="$SRC_DIR/dist"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-venv)     WITH_VENV=1; shift ;;
    --with-frontend) WITH_FRONTEND=1; shift ;;
    --version)       VERSION="$2"; shift 2 ;;
    --outdir)        OUTDIR="$2"; shift 2 ;;
    -h|--help)       sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) ARCH_TAG="amd64" ;;
  aarch64|arm64) ARCH_TAG="arm64" ;;
  *) ARCH_TAG="$ARCH" ;;
esac

STAGE_NAME="aurora-${VERSION}"
STAGE="$OUTDIR/${STAGE_NAME}"
TARBALL="$OUTDIR/${STAGE_NAME}-linux-${ARCH_TAG}.tar.gz"

C_RED='\033[1;31m'; C_GREEN='\033[1;32m'; C_BLUE='\033[1;34m'; C_OFF='\033[0m'
log() { printf "${C_BLUE}[package]${C_OFF} %s\n" "$*"; }
ok()  { printf "${C_GREEN}[OK]${C_OFF}     %s\n" "$*"; }
err() { printf "${C_RED}[ERROR]${C_OFF}   %s\n" "$*" >&2; }

command -v tar >/dev/null 2>&1 || { err "缺少 tar 命令"; exit 1; }

log "版本: $VERSION    架构: $ARCH_TAG"

# ---------- 1) 前端产物 ----------
if [[ $WITH_FRONTEND -eq 1 ]]; then
  log "构建前端 (npm ci && npm run build)"
  command -v npm >/dev/null 2>&1 || { err "未检测到 npm，无法构建前端（可用 --with-frontend 前先安装 Node 18+）"; exit 1; }
  cd "$SRC_DIR/frontend"
  if [[ -f package-lock.json ]]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
  npm run build
  ok "前端构建完成"
fi

if [[ ! -d "$SRC_DIR/frontend/dist" ]]; then
  err "未找到 frontend/dist（请先 npm run build，或加 --with-frontend 自动构建）"
  exit 1
fi

# ---------- 2) 准备暂存目录 ----------
log "准备暂存目录: $STAGE"
rm -rf "$STAGE" "$TARBALL"
mkdir -p "$STAGE"

# 后端：排除 venv 与缓存
mkdir -p "$STAGE/backend"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='venv' --exclude='.venv' --exclude='tests' \
    "$SRC_DIR/backend/" "$STAGE/backend/"
else
  (cd "$SRC_DIR/backend" && tar --exclude='__pycache__' --exclude='*.pyc' --exclude='venv' --exclude='tests' -cf - .) \
    | (cd "$STAGE/backend" && tar -xf -)
fi

# 前端产物
mkdir -p "$STAGE/frontend"
cp -r "$SRC_DIR/frontend/dist" "$STAGE/frontend/dist"

# 配置模板（严禁打包任何 *.local.yaml 密钥层）
mkdir -p "$STAGE/config"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --exclude='*.local.yaml' "$SRC_DIR/config/" "$STAGE/config/"
else
  (cd "$SRC_DIR/config" && find . -name '*.local.yaml' -prune -o -type f -print) \
    | while read -r f; do
        mkdir -p "$STAGE/config/$(dirname "$f")"
        cp "$SRC_DIR/config/$f" "$STAGE/config/$f"
      done
fi

# 部署相关
mkdir -p "$STAGE/deploy"
cp -r "$SRC_DIR/deploy/systemd" "$STAGE/deploy/" 2>/dev/null || true
cp -r "$SRC_DIR/deploy/docker" "$STAGE/deploy/" 2>/dev/null || true
cp "$SRC_DIR/deploy/nginx.conf" "$STAGE/deploy/" 2>/dev/null || true
cp "$SRC_DIR/deploy/docker-compose.yml" "$STAGE/deploy/" 2>/dev/null || true
cp "$SRC_DIR/deploy/.env.example" "$STAGE/deploy/" 2>/dev/null || true
cp "$SRC_DIR/deploy/gateway.env.example" "$STAGE/deploy/" 2>/dev/null || true
cp "$SRC_DIR/README.md" "$SRC_DIR/INSTALL.md" "$STAGE/" 2>/dev/null || true

# 随包分发离线安装脚本（目标机无需 git / npm / 网络）
cp "$SRC_DIR/scripts/install-tarball.sh" "$STAGE/install-tarball.sh"
chmod +x "$STAGE/install-tarball.sh"

# ---------- 3) 可选：预建虚拟环境 ----------
if [[ $WITH_VENV -eq 1 ]]; then
  log "预建Python 虚拟环境（目标机将免去编译）"
  PY_BIN="python3"
  for cand in python3.13 python3.12 python3.11 python3.10; do
    if command -v "$cand" >/dev/null 2>&1; then PY_BIN="$cand"; break; fi
  done
  "$PY_BIN" -m venv "$STAGE/backend/venv"
  "$STAGE/backend/venv/bin/pip" install --upgrade pip --quiet
  "$STAGE/backend/venv/bin/pip" install -r "$STAGE/backend/requirements.txt" --quiet
  ok "虚拟环境已内置（$("$STAGE/backend/venv/bin/python" -V 2>&1)）"
  if [[ "$ARCH_TAG" != "amd64" ]]; then
    printf "${C_RED}[WARN]${C_OFF}   %s\n" \
      "跨架构拷贝 venv 可能不可用：当前 ${ARCH_TAG}，请在目标机删除 venv 后重新安装依赖"
  fi
else
  ok "未内置 venv（目标机安装时自动创建）"
fi

# ---------- 4) 打包 ----------
log "生成压缩包: $TARBALL"
tar -czf "$TARBALL" -C "$OUTDIR" "$STAGE_NAME"

# 校验和
( cd "$OUTDIR" && sha256sum "$(basename "$TARBALL")" > "$(basename "$TARBALL").sha256" )

SIZE="$(du -h "$TARBALL" | cut -f1)"
ok "打包完成"
ok "  文件: $TARBALL ($SIZE)"
ok "  校验: cd $OUTDIR && sha256sum -c $(basename "$TARBALL").sha256"
ok "  安装: sudo tar -xzf $(basename "$TARBALL") -C /opt && sudo bash /opt/${STAGE_NAME}/install-tarball.sh"
