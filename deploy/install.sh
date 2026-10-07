#!/usr/bin/env bash
# =============================================================================
# install.sh —— AI 智能助手服务器一键安装 / 升级 / 卸载脚本
#
# 前置：已运行过 scripts/env-setup.sh 完成环境准备
#   （docker / nginx / python3.11+ / nodejs / git）
#
# 用法：
#   sudo bash deploy/install.sh                # 安装（幂等，重复执行即升级）
#   sudo bash deploy/install.sh --skip-frontend# 跳过前端构建（离线/仅后端）
#   sudo bash deploy/install.sh --no-start     # 安装但不启动服务
#   sudo bash deploy/install.sh --uninstall    # 卸载（保留数据）
#   sudo bash deploy/install.sh --purge        # 卸载并删除全部数据
#
# 本脚本完成以下步骤：
#   1. 校验以 root 运行
#   2. 创建目录骨架与 aiassistant 系统用户
#   3. 同步 backend 代码（排除 venv/__pycache__）
#   4. 创建 Python 虚拟环境并安装 backend/requirements.txt
#   5. 同步 config（保留 *.local.yaml 密钥层）
#   6. 构建前端（npm ci && npm run build）
#   7. 安装 systemd 服务与密钥环境文件（/etc/aiassistant/gateway.env）
#   8. 配置 nginx 模板并提示 HTTPS
#
# 幂等性：重复执行为「原地升级」，不会覆盖既有配置、会话与密钥。
# =============================================================================
set -euo pipefail

# 定位源码根目录（本脚本位于 <root>/deploy/install.sh）
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="/opt/aiassistant"
SERVICE_NAMES=(aiassistant-gateway vllm-main vllm-vision vllm-compress)
GATEWAY_ENV_DIR="/etc/aiassistant"
GATEWAY_ENV_FILE="${GATEWAY_ENV_DIR}/gateway.env"

# ---------- 参数解析 ----------
SKIP_FRONTEND=0
DO_START=0
DO_UNINSTALL=0
PURGE=0
for arg in "$@"; do
  case "$arg" in
    --skip-frontend) SKIP_FRONTEND=1 ;;
    --no-start)      DO_START=0 ;;
    --start)         DO_START=1 ;;
    --uninstall)     DO_UNINSTALL=1 ;;
    --purge)         PURGE=1 ;;
    -h|--help)       sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "未知参数: $arg（--help 查看用法）" >&2; exit 2 ;;
  esac
done
# 默认不自动启动：模型端点需用户先配置，避免启动即失败
DO_START=${DO_START}

C_RED='\033[1;31m'; C_GREEN='\033[1;32m'; C_BLUE='\033[1;34m'; C_YELLOW='\033[1;33m'; C_OFF='\033[0m'
log()  { printf "${C_BLUE}[install]${C_OFF} %s\n" "$*"; }
ok()   { printf "${C_GREEN}[OK]${C_OFF}     %s\n" "$*"; }
warn() { printf "${C_YELLOW}[!]${C_OFF}     %s\n" "$*"; }
err()  { printf "${C_RED}[ERROR]${C_OFF}   %s\n" "$*"; }

# ---------- 0) root 校验 ----------
if [[ $EUID -ne 0 ]]; then
  err "请以 root 运行：sudo bash deploy/install.sh"
  exit 1
fi

# ---------- 卸载流程 ----------
if [[ $DO_UNINSTALL -eq 1 ]]; then
  log "卸载 Aurora 网关"
  for svc in aiassistant-gateway vllm-main vllm-vision vllm-compress; do
    if systemctl list-unit-files | grep -q "^${svc}.service"; then
      systemctl disable --now "$svc" 2>/dev/null || true
      rm -f "/etc/systemd/system/${svc}.service"
      log "已移除服务 $svc"
    fi
  done
  systemctl daemon-reload
  systemctl reset-failed 2>/dev/null || true
  ok "systemd 服务已清理"

  rm -f /etc/nginx/conf.d/aiassistant.conf
  nginx -t >/dev/null 2>&1 && systemctl reload nginx 2>/dev/null || true
  ok "nginx 配置已移除"

  if id aiassistant >/dev/null 2>&1; then
    userdel aiassistant 2>/dev/null || true
    ok "系统用户 aiassistant 已删除"
  fi

  if [[ $PURGE -eq 1 ]]; then
    rm -rf "$TARGET" "$GATEWAY_ENV_DIR"
    ok "已删除数据目录 $TARGET 与 $GATEWAY_ENV_DIR（不可逆）"
  else
    ok "已保留数据目录 $TARGET（如需彻底删除请加 --purge）"
  fi
  ok "卸载完成"
  exit 0
fi

log "源码目录: $SRC_DIR"
log "目标目录: $TARGET"

# ---------- 1) 目录骨架 & aiassistant 用户 ----------
log "---- 创建目录结构与 aiassistant 用户 ----"
mkdir -p "$TARGET"/backend "$TARGET"/frontend "$TARGET"/config
mkdir -p "$TARGET"/data/sessions "$TARGET"/data/uploads "$TARGET"/logs
mkdir -p "$GATEWAY_ENV_DIR"
chmod 700 "$GATEWAY_ENV_DIR"

if ! id "aiassistant" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /opt/aiassistant --shell /usr/sbin/nologin aiassistant
  ok "已创建系统用户 aiassistant"
else
  log "用户 aiassistant 已存在，跳过创建"
fi

# ---------- 2) 拷贝后端代码 ----------
log "---- 同步后端代码 ----"
# 排除 venv 与缓存，避免覆盖运行中的虚拟环境
rsync -a --delete \
  --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.venv' --exclude='venv' \
  "$SRC_DIR/backend/" "$TARGET/backend/" 2>/dev/null \
  || { err "rsync 不可用，退回 cp"; cp -r "$SRC_DIR/backend/." "$TARGET/backend/"; }
cp -f "$SRC_DIR/backend/requirements.txt" "$TARGET/backend/requirements.txt"
ok "后端代码已同步到 $TARGET/backend"

# ---------- 3) 创建 venv 并安装依赖 ----------
log "---- 创建 Python 虚拟环境并安装依赖 ----"
PY_BIN="python3"
for cand in python3.13 python3.12 python3.11 python3.10; do
  if command -v "$cand" >/dev/null 2>&1; then PY_BIN="$cand"; break; fi
done
if ! command -v "$PY_BIN" >/dev/null 2>&1; then
  err "未找到 python3.10+，请先运行 scripts/env-setup.sh"
  exit 1
fi
if [[ ! -x "$TARGET/backend/venv/bin/python" ]]; then
  "$PY_BIN" -m venv "$TARGET/backend/venv" \
    || { err "创建虚拟环境失败（可能需安装 python3-venv：apt install python3-venv）"; exit 1; }
fi
cd "$TARGET/backend"
"$TARGET/backend/venv/bin/pip" install --upgrade pip --quiet
"$TARGET/backend/venv/bin/pip" install -r requirements.txt --quiet
ok "Python 依赖已安装（$("$TARGET/backend/venv/bin/python" -V 2>&1)）"

# ---------- 4) 拷贝 config（保留本机密钥层）----------
log "---- 同步 config 配置 ----"
# 注意：--exclude='*.local.yaml' 保留本机已有的密钥覆盖文件
rsync -a --exclude='*.local.yaml' "$SRC_DIR/config/" "$TARGET/config/" 2>/dev/null \
  || cp -r "$SRC_DIR/config/." "$TARGET/config/"
ok "config 已同步到 $TARGET/config（本机 *.local.yaml 密钥层已保留）"

# ---------- 5) 数据目录权限 ----------
log "---- 设置数据目录权限 ----"
chown -R aiassistant:aiassistant "$TARGET"/config "$TARGET"/data "$TARGET"/logs "$TARGET"/backend
chmod 750 "$TARGET"/data "$TARGET"/logs
ok "数据目录已就绪并授权给 aiassistant"

# ---------- 6) 构建前端 ----------
if [[ $SKIP_FRONTEND -eq 1 ]]; then
  warn "已跳过前端构建（--skip-frontend）"
else
  log "---- 构建前端 (npm ci && npm run build) ----"
  if ! command -v npm >/dev/null 2>&1; then
    warn "未检测到 npm，跳过前端构建（可安装 Node.js 18+ 后重跑）"
  else
    rsync -a --exclude='node_modules' --exclude='dist' \
      "$SRC_DIR/frontend/" "$TARGET/frontend/" 2>/dev/null \
      || cp -r "$SRC_DIR/frontend/." "$TARGET/frontend/"
    cd "$TARGET/frontend"
    # npm ci 会按 lockfile 精确安装并重建 node_modules
    if [[ -f package-lock.json ]]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
    npm run build
    chown -R aiassistant:aiassistant "$TARGET/frontend/dist"
    ok "前端已构建到 $TARGET/frontend/dist"
  fi
fi

# ---------- 7) 安装 systemd 服务与密钥环境文件 ----------
log "---- 安装 systemd 服务 ----"
for svc in "${SERVICE_NAMES[@]}"; do
  if [[ -f "$SRC_DIR/deploy/systemd/${svc}.service" ]]; then
    cp "$SRC_DIR/deploy/systemd/${svc}.service" "/etc/systemd/system/${svc}.service"
  fi
done
systemctl daemon-reload

# 密钥环境文件：不存在则从模板创建（不覆盖已有密钥）
if [[ -f "$SRC_DIR/deploy/gateway.env.example" && ! -f "$GATEWAY_ENV_FILE" ]]; then
  install -m 600 "$SRC_DIR/deploy/gateway.env.example" "$GATEWAY_ENV_FILE"
  ok "已创建密钥环境文件 $GATEWAY_ENV_FILE（权限 600）"
  warn "请编辑 $GATEWAY_ENV_FILE 填入 AI_ASSISTANT_API_KEY 后重启服务"
elif [[ -f "$GATEWAY_ENV_FILE" ]]; then
  log "密钥环境文件已存在，保留原值：$GATEWAY_ENV_FILE"
fi

systemctl enable aiassistant-gateway.service
systemctl enable vllm-main.service
systemctl enable vllm-vision.service
systemctl enable vllm-compress.service
ok "systemd 服务已 enable"

# ---------- 8) nginx ----------
log "---- nginx 配置提示 ----"
if command -v nginx >/dev/null 2>&1; then
  cp "$SRC_DIR/deploy/nginx.conf" /etc/nginx/conf.d/aiassistant.conf
  log "nginx 配置已写入 /etc/nginx/conf.d/aiassistant.conf"
  log "请编辑其中的 server_name 为真实域名后，执行："
  log "  sudo nginx -t && sudo systemctl reload nginx"
else
  log "未检测到 nginx，跳过（可运行 scripts/env-setup.sh 安装，或直接访问 127.0.0.1:8080）"
fi

ok "======================================================"
ok " 安装完成。"
ok " 1. 配置密钥：sudo vi ${GATEWAY_ENV_FILE}（填 AI_ASSISTANT_API_KEY）"
ok " 2. 配置模型：编辑 ${TARGET}/config/models.local.yaml（base_url / api_key）"
ok "    或先启动模型服务：systemctl start vllm-main vllm-vision vllm-compress"
ok " 3. 启动网关：sudo systemctl restart aiassistant-gateway"
ok " 4. 验证：curl http://127.0.0.1:8080/api/health"
ok " 5. 查看日志：journalctl -u aiassistant-gateway -f"
ok " 6. HTTPS（可选）：sudo certbot --nginx -d 你的域名"
ok "======================================================"