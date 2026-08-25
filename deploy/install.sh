#!/usr/bin/env bash
# =============================================================================
# install.sh —— AI 智能助手服务器一键安装脚本
#
# 前置：已在本目录运行过 scripts/env-setup.sh 完成环境准备
#   （docker / nginx / python3.11+ / nodejs / git）
#
# 用法：sudo bash deploy/install.sh
#
# 本脚本完成以下步骤：
#   1. 校验以 root 运行
#   2. 拷贝 backend 代码到 /opt/aiassistant
#   3. 创建 python 虚拟环境并发 yaml 依赖安装 backend/requirements.txt
#   4. 拷贝 config 配置到 /opt/aiassistant/config
#   5. 创建数据目录与 aiassistant 系统用户
#   6. 构建前端（npm ci && npm run build）到 /opt/aiassistant/frontend/dist
#   7. 安装并 enable 各 systemd 服务
#   8. 提示手动配置 nginx / vLLM 模型参数
# =============================================================================
set -euo pipefail

# 定位源码根目录（本脚本位于 <root>/deploy/install.sh）
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="/opt/aiassistant"
SERVICE_NAMES=(aiassistant-gateway vllm-main vllm-vision vllm-compress)

C_RED='\033[1;31m'; C_GREEN='\033[1;32m'; C_BLUE='\033[1;34m'; C_OFF='\033[0m'
log() { printf "${C_BLUE}[install]${C_OFF} %s\n" "$*"; }
ok()  { printf "${C_GREEN}[OK]${C_OFF}     %s\n" "$*"; }
err() { printf "${C_RED}[ERROR]${C_OFF}   %s\n" "$*"; }

# ---------- 0) root 校验 ----------
if [[ $EUID -ne 0 ]]; then
  err "请以 root 运行：sudo bash deploy/install.sh"
  exit 1
fi

log "源码目录: $SRC_DIR"
log "目标目录: $TARGET"

# ---------- 1) 目录骨架 & aiassistant 用户 ----------
log "---- 创建目录结构与 aiassistant 用户 ----"
mkdir -p "$TARGET"/backend
mkdir -p "$TARGET"/frontend
mkdir -p "$TARGET"/config
mkdir -p "$TARGET"/data/{sessions,uploads}
mkdir -p "$TARGET"/logs

# 创建专用运行用户（幂等：已存在则跳过）
if ! id "aiassistant" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /opt/aiassistant --shell /usr/sbin/nologin aiassistant
  ok "已创建系统用户 aiassistant"
else
  log "用户 aiassistant 已存在，跳过创建"
fi

# ---------- 2) 拷贝后端代码 ----------
log "---- 拷贝后端代码 ----"
rsync -a --delete --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.venv' --exclude='venv' \
  "$SRC_DIR/backend/" "$TARGET/backend/" 2>/dev/null \
  || { err "rsync 不可用，退回 cp"; cp -r "$SRC_DIR/backend/." "$TARGET/backend/"; }
# 保证 requirements.txt 一定存在
cp -f "$SRC_DIR/backend/requirements.txt" "$TARGET/backend/requirements.txt"
ok "后端代码已拷贝到 $TARGET/backend"

# ---------- 3) 创建 venv 并安装依赖 ----------
log "---- 创建 Python 虚拟环境并安装依赖 ----"
if [[ ! -x "$TARGET/backend/venv/bin/python" ]]; then
  python3 -m venv "$TARGET/backend/venv"
fi
cd "$TARGET/backend"
# 升级 pip 后按 requirements 安装（幂等）
"$TARGET/backend/venv/bin/pip" install --upgrade pip
"$TARGET/backend/venv/bin/pip" install -r requirements.txt
ok "Python 依赖已安装"

# ---------- 4) 拷贝 config ----------
log "---- 拷贝 config 配置 ----"
rsync -a "$SRC_DIR/config/" "$TARGET/config/" 2>/dev/null \
  || cp -r "$SRC_DIR/config/." "$TARGET/config/"
ok "config 已拷贝到 $TARGET/config"

# ---------- 5) 数据目录权限 ----------
log "---- 设置数据目录权限 ----"
mkdir -p "$TARGET/data"/{sessions,uploads}
chown -R aiassistant:aiassistant "$TARGET"/config "$TARGET"/data "$TARGET"/logs "$TARGET"/backend
ok "数据目录已就绪并授权给 aiassistant"

# ---------- 6) 构建前端 ----------
log "---- 构建前端 (npm ci && npm run build) ----"
# 同步前端源码（排除 node_modules/dist 后重新干净构建）
rsync -a --exclude='node_modules' --exclude='dist' \
  "$SRC_DIR/frontend/" "$TARGET/frontend/" 2>/dev/null \
  || cp -r "$SRC_DIR/frontend/." "$TARGET/frontend/"
cd "$TARGET/frontend"
# 幂等/干净安装：npm ci 会删除 node_modules 后按 lockfile 精确安装
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi
npm run build
ok "前端已构建到 $TARGET/frontend/dist"

# ---------- 7) 安装 systemd 服务 ----------
log "---- 安装并启用 systemd 服务 ----"
for svc in "${SERVICE_NAMES[@]}"; do
  cp "$SRC_DIR/deploy/systemd/${svc}.service" "/etc/systemd/system/${svc}.service"
  systemctl daemon-reload
done
systemctl enable aiassistant-gateway.service
systemctl enable vllm-main.service
systemctl enable vllm-vision.service
systemctl enable vllm-compress.service
ok "systemd 服务已安装并 enable（未 start，请配置 vLLM 模型后手动启动）"

# ---------- 8) nginx ----------
log "---- nginx 配置提示 ----"
if command -v nginx >/dev/null 2>&1; then
  cp "$SRC_DIR/deploy/nginx.conf" /etc/nginx/conf.d/aiassistant.conf
  # 先不重启 nginx，交由用户确认域名/HTTPS 后再 nginx -t && reload
  log "nginx 配置已在 /etc/nginx/conf.d/aiassistant.conf"
  log "请编辑其中的 server_name 为真实域名后，执行："
  log "  sudo nginx -t && sudo systemctl reload nginx"
else
  log "未检测到 nginx，跳过安装（请运行 scripts/env-setup.sh 安装）"
fi

ok "======================================================"
ok " 安装完成。"
ok " 1. 请先编辑 /etc/systemd/system/vllm-*.service 中的模型名/端口(8001/8002/8003)"
ok " 2. 然后启动：systemctl start vllm-main vllm-vision vllm-compress"
ok " 3. 启动网关：systemctl start aiassistant-gateway"
ok " 4. 启用 HTTPS（可选）：sudo certbot --nginx -d 你的域名"
ok "======================================================"