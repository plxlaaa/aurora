#!/usr/bin/env bash
# =============================================================================
# install-wsl2-linux.sh —— WSL2 内的 Linux 侧安装脚本
#
# 通常不直接调用：在 Windows 侧执行 deploy/wsl2/install-wsl2.ps1，
# 由它通过 `wsl -d <distro> -- bash -lc` 转调本脚本。
#
# 与 deploy/install.sh 的差异：
#   - 不强制创建 aiassistant 系统用户（WSL2 默认单用户 root/sudo 即可）
#   - 自动放开 Windows 宿主机访问所需的配置
#   - 端口同时在 WSL 内与 Windows 侧可达（WSL2 localhost 转发）
#
# 用法：
#   sudo bash deploy/wsl2/install-wsl2-linux.sh
#   sudo bash deploy/wsl2/install-wsl2-linux.sh --skip-frontend
#   sudo bash deploy/wsl2/install-wsl2-linux.sh --install-dir /opt/aurora --port 9090
#   sudo bash deploy/wsl2/install-wsl2-linux.sh --uninstall [--purge]
# =============================================================================
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET="/opt/aiassistant"
PORT=8080
GATEWAY_ENV_DIR="/etc/aiassistant"
GATEWAY_ENV_FILE="${GATEWAY_ENV_DIR}/gateway.env"
SERVICE_NAME="aiassistant-gateway"

SKIP_FRONTEND=0
DO_UNINSTALL=0
PURGE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-frontend) SKIP_FRONTEND=1; shift ;;
    --install-dir)   TARGET="$2"; shift 2 ;;
    --port)          PORT="$2"; shift 2 ;;
    --uninstall)     DO_UNINSTALL=1; shift ;;
    --purge)         PURGE=1; shift ;;
    -h|--help)       sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数: $1（--help 查看用法）" >&2; exit 2 ;;
  esac
done

C_RED='\033[1;31m'; C_GREEN='\033[1;32m'; C_BLUE='\033[1;34m'; C_YELLOW='\033[1;33m'; C_OFF='\033[0m'
log()  { printf "${C_BLUE}[wsl2]${C_OFF} %s\n" "$*"; }
ok()   { printf "${C_GREEN}[OK]${C_OFF}     %s\n" "$*"; }
warn() { printf "${C_YELLOW}[!]${C_OFF}     %s\n" "$*"; }
err()  { printf "${C_RED}[ERROR]${C_OFF}   %s\n" "$*" >&2; }

# WSL2 环境识别（含 WSL1 / 容器 / 真实 Linux 的兼容处理）
detect_wsl() {
  if [[ -n "${WSL_DISTRO_NAME:-}" ]]; then
    echo "WSL_DISTRO=${WSL_DISTRO_NAME}"
  elif grep -qi microsoft /proc/version 2>/dev/null; then
    echo "WSL_DISTRO=unknown"
  else
    echo "WSL_DISTRO=none"
  fi
}
WSL_INFO="$(detect_wsl)"
if [[ "$WSL_INFO" == "WSL_DISTRO=none" ]]; then
  warn "未检测到 WSL 环境，将按普通 Linux 安装（如需 WSL2 托管请用 deploy/wsl2/install-wsl2.ps1）"
fi

if [[ $EUID -ne 0 ]]; then
  err "请以 root 运行：sudo bash $0"
  exit 1
fi

# ---------- 卸载 ----------
if [[ $DO_UNINSTALL -eq 1 ]]; then
  log "卸载 Aurora 网关（WSL2）"
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}.service"; then
    systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
    ok "systemd 服务已清理"
  fi
  if [[ $PURGE -eq 1 ]]; then
    rm -rf "$TARGET" "$GATEWAY_ENV_DIR"
    ok "已删除 $TARGET 与 $GATEWAY_ENV_DIR（不可逆）"
  else
    ok "已保留数据目录 $TARGET（--purge 可彻底删除）"
  fi
  ok "卸载完成"
  exit 0
fi

log "源码目录: $SRC_DIR"
log "安装目录: $TARGET"
log "监听端口: $PORT"

# ---------- 1) 目录骨架 ----------
mkdir -p "$TARGET"/backend "$TARGET"/frontend "$TARGET"/config
mkdir -p "$TARGET"/data/sessions "$TARGET"/data/uploads "$TARGET"/logs
mkdir -p "$GATEWAY_ENV_DIR"
chmod 700 "$GATEWAY_ENV_DIR"
ok "目录结构就绪"

# ---------- 2) 同步后端 ----------
log "---- 同步后端代码 ----"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete --exclude='__pycache__' --exclude='*.pyc' --exclude='.venv' --exclude='venv' \
    "$SRC_DIR/backend/" "$TARGET/backend/"
else
  rm -rf "$TARGET/backend/app"
  cp -r "$SRC_DIR/backend/app" "$TARGET/backend/app"
fi
cp -f "$SRC_DIR/backend/requirements.txt" "$TARGET/backend/requirements.txt"
ok "后端代码已同步"

# ---------- 3) venv ----------
log "---- 创建虚拟环境 ----"
PY_BIN="python3"
for cand in python3.13 python3.12 python3.11 python3.10; do
  if command -v "$cand" >/dev/null 2>&1; then PY_BIN="$cand"; break; fi
done
command -v "$PY_BIN" >/dev/null 2>&1 || { err "未找到 python3.10+"; exit 1; }
[[ -x "$TARGET/backend/venv/bin/python" ]] \
  || "$PY_BIN" -m venv "$TARGET/backend/venv" \
  || { err "创建 venv 失败（可能需 apt install python3-venv）"; exit 1; }
"$TARGET/backend/venv/bin/pip" install --upgrade pip --quiet
"$TARGET/backend/venv/bin/pip" install -r "$TARGET/backend/requirements.txt" --quiet
PY_VER="$("$TARGET/backend/venv/bin/python" -V 2>&1)"
ok "依赖已安装（${PY_VER}）"

# ---------- 4) 配置 ----------
if command -v rsync >/dev/null 2>&1; then
  rsync -a --exclude='*.local.yaml' "$SRC_DIR/config/" "$TARGET/config/"
else
  cp -r "$SRC_DIR/config/." "$TARGET/config/"
fi
ok "config 已同步（保留本机 *.local.yaml 密钥层）"

# ---------- 5) 前端 ----------
if [[ $SKIP_FRONTEND -eq 1 ]]; then
  warn "已跳过前端构建（--skip-frontend）"
elif ! command -v npm >/dev/null 2>&1; then
  warn "未检测到 npm，跳过前端构建（可加 --skip-frontend 明确跳过）"
else
  log "---- 构建前端 ----"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --exclude='node_modules' --exclude='dist' "$SRC_DIR/frontend/" "$TARGET/frontend/"
  else
    mkdir -p "$TARGET/frontend"
    cp -r "$SRC_DIR/frontend/." "$TARGET/frontend/"
  fi
  cd "$TARGET/frontend"
  if [[ -f package-lock.json ]]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
  npm run build
  ok "前端已构建"
fi

# ---------- 6) 密钥环境文件 ----------
if [[ ! -f "$GATEWAY_ENV_FILE" ]]; then
  if [[ -f "$SRC_DIR/deploy/gateway.env.example" ]]; then
    install -m 600 "$SRC_DIR/deploy/gateway.env.example" "$GATEWAY_ENV_FILE"
  else
    cat > "$GATEWAY_ENV_FILE" <<ENVEOF
AI_ASSISTANT_HOST=0.0.0.0
AI_ASSISTANT_PORT=${PORT}
AI_ASSISTANT_PRODUCTION=true
AI_ASSISTANT_LOG_LEVEL=INFO
AI_ASSISTANT_FRONTEND_DIR=${TARGET}/frontend/dist
ENVEOF
    chmod 600 "$GATEWAY_ENV_FILE"
  fi
  # WSL2 需监听 0.0.0.0，Windows 宿主机才能通过 localhost 转发访问
  if ! grep -q '^AI_ASSISTANT_HOST=' "$GATEWAY_ENV_FILE"; then
    printf 'AI_ASSISTANT_HOST=0.0.0.0\n' >> "$GATEWAY_ENV_FILE"
  else
    sed -i 's/^AI_ASSISTANT_HOST=.*/AI_ASSISTANT_HOST=0.0.0.0/' "$GATEWAY_ENV_FILE"
  fi
  if ! grep -q '^AI_ASSISTANT_PORT=' "$GATEWAY_ENV_FILE"; then
    printf 'AI_ASSISTANT_PORT=%s\n' "$PORT" >> "$GATEWAY_ENV_FILE"
  fi
  ok "已创建 $GATEWAY_ENV_FILE（已设为 0.0.0.0:${PORT} 以便宿主机访问）"
  warn "请编辑该文件填入 AI_ASSISTANT_API_KEY 后重启服务"
else
  log "密钥环境文件已存在，保留原值"
fi

# ---------- 7) systemd ----------
if command -v systemctl >/dev/null 2>&1; then
  log "---- 注册 systemd 服务 ----"
  UNIT_SRC="$SRC_DIR/deploy/systemd/${SERVICE_NAME}.service"
  if [[ -f "$UNIT_SRC" ]]; then
    cp "$UNIT_SRC" "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    ok "服务已 enable（需先配置模型端点再启动）"
  else
    warn "未找到 ${SERVICE_NAME}.service，跳过"
  fi
else
  warn "未检测到 systemd，跳过服务注册（可手动运行 uvicorn）"
fi

ok "======================================================"
ok " WSL2 部署完成"
ok " 1. 配置密钥：sudo vi ${GATEWAY_ENV_FILE}"
ok " 2. 配置模型：编辑 ${TARGET}/config/models.local.yaml"
ok " 3. 启动服务：sudo systemctl restart ${SERVICE_NAME}"
ok " 4. 验证：curl http://127.0.0.1:${PORT}/api/health"
ok " 5. Windows 侧访问：http://localhost:${PORT}"
ok " 注意：若无法访问，请在 Windows 管理员 PowerShell 执行："
ok "   wsl --shutdown && netsh interface portproxy add v4tov4 listenport=${PORT} listenaddress=0.0.0.0 connectport=${PORT} connectaddress=(wsl hostname -I | awk '{print \$1}')"
ok "======================================================"
